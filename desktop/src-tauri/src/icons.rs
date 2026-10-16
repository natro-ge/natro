//! Apps' own icons for the dock's steps ("Opening Spotify" with Spotify's icon),
//! from the Start menu: the helper finds the app by name, Windows draws its icon.

use std::collections::HashMap;
use std::sync::{Arc, Mutex};

use base64::Engine;
use windows::Win32::Foundation::SIZE;
use windows::Win32::Graphics::Gdi::{
    BI_RGB, BITMAP, BITMAPINFO, BITMAPINFOHEADER, DIB_RGB_COLORS, DeleteObject, GetDC, GetDIBits,
    GetObjectW, HBITMAP, ReleaseDC,
};
use windows::Win32::System::Com::{COINIT_APARTMENTTHREADED, CoInitializeEx, IBindCtx};
use windows::Win32::UI::Shell::{
    IShellItemImageFactory, SHCreateItemFromParsingName, SIIGBF_BIGGERSIZEOK, SIIGBF_ICONONLY,
};
use windows::core::HSTRING;

use crate::helper::Helper;

/// Pixels: shown at 16 logical pixels, so sharp up to 200% scaling.
const SIZE_PX: i32 = 32;

pub struct Icons {
    helper: Arc<Helper>,
    /// By app name (lowercase): the icon as a data URL, or None for apps that aren't installed.
    known: Mutex<HashMap<String, Option<String>>>,
}

impl Icons {
    pub fn new(helper: Arc<Helper>) -> Icons {
        Icons {
            helper,
            known: Mutex::default(),
        }
    }

    /// The icon if it's already known: Some(None) when the app isn't installed, None when not asked yet.
    pub fn known(&self, app: &str) -> Option<Option<String>> {
        self.known.lock().unwrap().get(&app.to_lowercase()).cloned()
    }

    /// The app's icon as a PNG data URL, if it's installed. Blocking: call on a thread of its own.
    pub fn find(&self, app: &str) -> Option<String> {
        if let Some(known) = self.known(app) {
            return known;
        }
        // None: the helper couldn't be asked (not running yet): don't remember that.
        let app_id = tauri::async_runtime::block_on(self.helper.find_app(app))?;
        let icon = app_id.and_then(|id| shell_icon(&id));
        self.known
            .lock()
            .unwrap()
            .insert(app.to_lowercase(), icon.clone());
        icon
    }
}

/// A Start menu app's icon (its AppID, as `shell:AppsFolder` knows it), as a PNG data URL.
fn shell_icon(app_id: &str) -> Option<String> {
    unsafe {
        let _ = CoInitializeEx(None, COINIT_APARTMENTTHREADED);
        let path = HSTRING::from(format!("shell:AppsFolder\\{app_id}"));
        let item: IShellItemImageFactory =
            SHCreateItemFromParsingName(&path, None::<&IBindCtx>).ok()?;
        let bitmap = item
            .GetImage(
                SIZE {
                    cx: SIZE_PX,
                    cy: SIZE_PX,
                },
                SIIGBF_ICONONLY | SIIGBF_BIGGERSIZEOK,
            )
            .ok()?;
        let pixels = rgba(bitmap);
        let _ = DeleteObject(bitmap.into());
        let (width, height, pixels) = pixels?;
        png_data_url(width, height, &pixels)
    }
}

/// The bitmap's pixels as straight (not premultiplied) RGBA.
unsafe fn rgba(bitmap: HBITMAP) -> Option<(u32, u32, Vec<u8>)> {
    let mut size = BITMAP::default();
    let read = unsafe {
        GetObjectW(
            bitmap.into(),
            size_of::<BITMAP>() as i32,
            Some((&mut size as *mut BITMAP).cast()),
        )
    };
    let (width, height) = (size.bmWidth, size.bmHeight.abs());
    if read == 0 || width <= 0 || height <= 0 {
        return None;
    }
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
    let mut pixels = vec![0u8; (width * height * 4) as usize];
    let lines = unsafe {
        let screen = GetDC(None);
        let lines = GetDIBits(
            screen,
            bitmap,
            0,
            height as u32,
            Some(pixels.as_mut_ptr().cast()),
            &mut info,
            DIB_RGB_COLORS,
        );
        ReleaseDC(None, screen);
        lines
    };
    if lines != height {
        return None;
    }
    let has_alpha = pixels.chunks_exact(4).any(|p| p[3] != 0);
    for p in pixels.chunks_exact_mut(4) {
        let (b, g, r, a) = (p[0], p[1], p[2], if has_alpha { p[3] } else { 255 });
        // Windows hands icons over premultiplied; PNG wants straight alpha.
        let straight = |c: u8| {
            if a == 0 {
                0
            } else {
                ((c as u32 * 255 + a as u32 / 2) / a as u32).min(255) as u8
            }
        };
        p.copy_from_slice(&[straight(r), straight(g), straight(b), a]);
    }
    Some((width as u32, height as u32, pixels))
}

fn png_data_url(width: u32, height: u32, rgba: &[u8]) -> Option<String> {
    let mut file = Vec::new();
    {
        let mut encoder = png::Encoder::new(&mut file, width, height);
        encoder.set_color(png::ColorType::Rgba);
        encoder.set_depth(png::BitDepth::Eight);
        let mut writer = encoder.write_header().ok()?;
        writer.write_image_data(rgba).ok()?;
    }
    Some(format!(
        "data:image/png;base64,{}",
        base64::engine::general_purpose::STANDARD.encode(file)
    ))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn png_data_url_is_a_png() {
        let url = png_data_url(2, 1, &[255, 0, 0, 255, 0, 0, 255, 128]).unwrap();
        let png = base64::engine::general_purpose::STANDARD
            .decode(url.strip_prefix("data:image/png;base64,").unwrap())
            .unwrap();
        assert_eq!(&png[..8], b"\x89PNG\r\n\x1a\n");
    }

    /// By hand, with an installed app's Start menu AppID (Get-StartApps):
    /// NATRO_TEST_APP_ID=... cargo test -- --ignored draws_an_apps_icon
    /// writes its icon to natro-icon-test.png in the temp folder.
    #[test]
    #[ignore]
    fn draws_an_apps_icon() {
        let id = std::env::var("NATRO_TEST_APP_ID").expect("NATRO_TEST_APP_ID");
        let url = shell_icon(&id).expect("an icon");
        let png = base64::engine::general_purpose::STANDARD
            .decode(url.strip_prefix("data:image/png;base64,").unwrap())
            .unwrap();
        std::fs::write(std::env::temp_dir().join("natro-icon-test.png"), png).unwrap();
    }
}
