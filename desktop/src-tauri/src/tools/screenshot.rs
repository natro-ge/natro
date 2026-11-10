//! Screenshots of the PC's screen, for the screenshot tool (uia.rs): the monitor of a window,
//! copied as Windows shows it, shrunk to at most SHOT_PIXELS on its longer side and made a JPEG,
//! as the Python helper's Pillow did.

use windows::Win32::Foundation::{HWND, RECT};
use windows::Win32::Graphics::Gdi::{
    BI_RGB, BITMAPINFO, BITMAPINFOHEADER, BitBlt, CreateCompatibleBitmap, CreateCompatibleDC, DIB_RGB_COLORS, DeleteDC,
    DeleteObject, GetDC, GetDIBits, GetMonitorInfoW, MONITOR_DEFAULTTOPRIMARY, MONITORINFO, MonitorFromWindow,
    ReleaseDC, SRCCOPY, SelectObject,
};

/// The longest side at most this, as JPEG, so one is a few hundred KB.
const SHOT_PIXELS: u32 = 1920;
const SHOT_QUALITY: u8 = 80;

/// The monitor a window is on, as JPEG. On a thread aware of each monitor's DPI (uia.rs's), so
/// it's every pixel.
pub fn of_monitor(window: HWND) -> Result<Vec<u8>, String> {
    let area = unsafe {
        let monitor = MonitorFromWindow(window, MONITOR_DEFAULTTOPRIMARY);
        let mut info = MONITORINFO { cbSize: size_of::<MONITORINFO>() as u32, ..Default::default() };
        let _ = GetMonitorInfoW(monitor, &mut info);
        info.rcMonitor
    };
    let (width, height, rgb) = capture(area).ok_or("The screen couldn't be copied.")?;
    let (shown_width, shown_height) = thumbnail_size(width, height, SHOT_PIXELS);
    let rgb = shrink(&rgb, width, height, shown_width, shown_height);
    let mut jpeg = Vec::new();
    let mut encoder = jpeg_encoder::Encoder::new(&mut jpeg, SHOT_QUALITY);
    encoder.set_optimized_huffman_tables(true);
    encoder
        .encode(&rgb, shown_width as u16, shown_height as u16, jpeg_encoder::ColorType::Rgb)
        .map_err(|e| format!("The screenshot couldn't be made: {e}"))?;
    Ok(jpeg)
}

/// What's on screen in an area, as RGB (its own always-on-top see-through windows, like Natro's,
/// left out, as Pillow's grab left them).
fn capture(area: RECT) -> Option<(u32, u32, Vec<u8>)> {
    let (width, height) = (area.right - area.left, area.bottom - area.top);
    if width <= 0 || height <= 0 {
        return None;
    }
    let mut pixels = vec![0u8; (width * height * 4) as usize];
    let lines = unsafe {
        let screen = GetDC(None);
        let memory = CreateCompatibleDC(Some(screen));
        let bitmap = CreateCompatibleBitmap(screen, width, height);
        let old = SelectObject(memory, bitmap.into());
        let copied = BitBlt(memory, 0, 0, width, height, Some(screen), area.left, area.top, SRCCOPY).is_ok();
        SelectObject(memory, old);
        let mut info = BITMAPINFO {
            bmiHeader: BITMAPINFOHEADER {
                biSize: size_of::<BITMAPINFOHEADER>() as u32,
                biWidth: width,
                biHeight: -height, // top-down
                biPlanes: 1,
                biBitCount: 32,
                biCompression: BI_RGB.0,
                ..Default::default()
            },
            ..Default::default()
        };
        let lines = if copied {
            GetDIBits(memory, bitmap, 0, height as u32, Some(pixels.as_mut_ptr().cast()), &mut info, DIB_RGB_COLORS)
        } else {
            0
        };
        let _ = DeleteObject(bitmap.into());
        let _ = DeleteDC(memory);
        ReleaseDC(None, screen);
        lines
    };
    if lines != height {
        return None;
    }
    let rgb = pixels.chunks_exact(4).flat_map(|bgra| [bgra[2], bgra[1], bgra[0]]).collect();
    Some((width as u32, height as u32, rgb))
}

/// The size a picture is shown at, at most longest on its longer side, as Pillow's thumbnail
/// chooses it (the nearest whole size to the same shape).
fn thumbnail_size(width: u32, height: u32, longest: u32) -> (u32, u32) {
    if longest >= width && longest >= height {
        return (width, height);
    }
    let aspect = width as f64 / height as f64;
    let nearest = |exact: f64, off: &dyn Fn(f64) -> f64| -> u32 {
        let (floor, ceil) = (exact.floor(), exact.ceil());
        let chosen = if off(floor) <= off(ceil) { floor } else { ceil };
        (chosen as u32).max(1)
    };
    let side = longest as f64;
    if 1.0 >= aspect {
        let x = nearest(side * aspect, &|n| (aspect - n / side).abs());
        (x, longest)
    } else {
        let y = nearest(side / aspect, &|n| if n == 0.0 { 0.0 } else { (aspect - side / n).abs() });
        (longest, y)
    }
}

/// A smaller copy of an RGB picture, each pixel the average of the area it covers.
fn shrink(rgb: &[u8], width: u32, height: u32, to_width: u32, to_height: u32) -> Vec<u8> {
    if (to_width, to_height) == (width, height) {
        return rgb.to_vec();
    }
    // For each new column (or row), the old ones it covers and how much of each.
    let spans = |from: u32, to: u32| -> Vec<Vec<(usize, f64)>> {
        let scale = from as f64 / to as f64;
        (0..to)
            .map(|i| {
                let (start, end) = (i as f64 * scale, (i + 1) as f64 * scale);
                (start.floor() as usize..(end.ceil() as usize).min(from as usize))
                    .map(|j| (j, (end.min(j as f64 + 1.0) - start.max(j as f64)).max(0.0)))
                    .filter(|(_, weight)| *weight > 0.0)
                    .collect()
            })
            .collect()
    };
    let (columns, rows) = (spans(width, to_width), spans(height, to_height));
    // Columns first, then rows.
    let mut narrow = vec![0f64; (to_width * height * 3) as usize];
    for y in 0..height as usize {
        for (x, span) in columns.iter().enumerate() {
            let total: f64 = span.iter().map(|(_, w)| w).sum();
            for channel in 0..3 {
                let sum: f64 = span.iter().map(|&(j, w)| rgb[(y * width as usize + j) * 3 + channel] as f64 * w).sum();
                narrow[(y * to_width as usize + x) * 3 + channel] = sum / total;
            }
        }
    }
    let mut out = vec![0u8; (to_width * to_height * 3) as usize];
    for (y, span) in rows.iter().enumerate() {
        let total: f64 = span.iter().map(|(_, w)| w).sum();
        for x in 0..to_width as usize {
            for channel in 0..3 {
                let sum: f64 = span.iter().map(|&(j, w)| narrow[(j * to_width as usize + x) * 3 + channel] * w).sum();
                out[(y * to_width as usize + x) * 3 + channel] = (sum / total).round().clamp(0.0, 255.0) as u8;
            }
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn screenshots_are_shrunk_as_pillow_did() {
        assert_eq!(thumbnail_size(2560, 1440, 1920), (1920, 1080));
        assert_eq!(thumbnail_size(1920, 1080, 1920), (1920, 1080));
        assert_eq!(thumbnail_size(1366, 768, 1920), (1366, 768));
        assert_eq!(thumbnail_size(1440, 2560, 1920), (1080, 1920));
        assert_eq!(thumbnail_size(3000, 2000, 1920), (1920, 1280));
        // Two pixels become one, their average.
        assert_eq!(shrink(&[0, 0, 0, 255, 255, 255], 2, 1, 1, 1), [128, 128, 128]);
    }
}
