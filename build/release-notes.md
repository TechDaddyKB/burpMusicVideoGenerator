The burps are back, and this time they've brought their own FFmpeg. Nothing else to install: download, open, belch.

## Which file do I want?

| You're on | Download |
| --- | --- |
| Windows 10/11 (64-bit) | `burp-music-video-generator-{{VERSION}}-windows-x64.zip`, unzip, run `burp-music-video-generator.exe` |
| macOS (Apple silicon) | `burp-music-video-generator-{{VERSION}}-macos-arm64.dmg`, drag to Applications |
| Ubuntu, Debian, Mint, Pop!_OS | `burp-music-video-generator_{{VERSION}}_amd64.deb` |
| Any Linux, one file | `burp-music-video-generator-{{VERSION}}-linux-x64.AppImage` (mark it executable and run) |
| Any Linux, sandboxed | `burp-music-video-generator-{{VERSION}}-linux-x64.flatpak` (`flatpak install --user <file>`) |
| Any Linux, plain folder | `burp-music-video-generator-{{VERSION}}-linux-x64.tar.gz` |

## Standard or TensorFlow edition?

Every file comes in two editions. **Most people want the standard one** (the files without `-tensorflow`).

| | Standard | TensorFlow (`-tensorflow`) |
| --- | --- | --- |
| Download size | ~100 MB | ~500 MB to 1 GB |
| Burp detector | Built-in NumPy copy of Google's YAMNet | Google's original TensorFlow YAMNet |
| Results | Same model and weights; matches TensorFlow's scores to within 0.0001 | The reference |
| Detection on the GPU | No (CPU, about a second per minute of video) | Linux with an NVIDIA GPU and CUDA 12 + cuDNN 9 installed; CPU elsewhere |
| Video encoding on the GPU | Yes (NVIDIA, AMD, Intel, Apple, Vulkan), falling back to the CPU | Same |

Pick TensorFlow only if you have a Linux machine with an NVIDIA GPU and CUDA set up, and you're processing
mountains of broadcasts. Everyone else: standard. Your burps will sound exactly as dignified either way.

## First run notes

- **Windows:** the app is signed. Brand-new releases can still get a SmartScreen "Windows protected your PC"
  notice until the signature builds a reputation; click **More info**, then **Run anyway**.
- **macOS:** the app isn't notarized by Apple yet. The first time, right-click it and choose **Open**
  (or allow it under System Settings > Privacy & Security).
- **Linux AppImage:** `chmod +x` the file first. **Flatpak:** needs Flathub configured for the runtime.

Check your download against `SHA256SUMS.txt` if you like your burps verified.

The bundled FFmpeg is GPL software built from source by this repository's workflow; its sources and build
settings are listed in `ffmpeg/SOURCES.txt` inside each download.
