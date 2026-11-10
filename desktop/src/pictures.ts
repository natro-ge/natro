// Pictures he sends into the chat (pasted, or the picture button): the longest side at most this, as JPEG.
const PICTURE_PIXELS = 2048

/** A picture, made small enough to send quickly, as a JPEG data URL. */
export async function pictureData(file: Blob) {
  const bitmap = await createImageBitmap(file)
  const scale = Math.min(1, PICTURE_PIXELS / Math.max(bitmap.width, bitmap.height))
  const canvas = document.createElement('canvas')
  canvas.width = Math.max(1, Math.round(bitmap.width * scale))
  canvas.height = Math.max(1, Math.round(bitmap.height * scale))
  const context = canvas.getContext('2d')!
  context.fillStyle = '#fff' // a transparent picture on white, not black
  context.fillRect(0, 0, canvas.width, canvas.height)
  context.drawImage(bitmap, 0, 0, canvas.width, canvas.height)
  bitmap.close()
  return canvas.toDataURL('image/jpeg', 0.85)
}
