fn main() {
    // sherpa-onnx's library is loaded when she first speaks (voice.rs): Natro starts without it.
    println!("cargo:rustc-link-arg=/DELAYLOAD:sherpa-onnx-c-api.dll");
    println!("cargo:rustc-link-lib=delayimp");
    tauri_build::build()
}
