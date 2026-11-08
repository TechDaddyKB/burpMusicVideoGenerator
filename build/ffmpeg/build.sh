#!/usr/bin/env bash
# Builds the app's own small ffmpeg and ffprobe: only what Burp Music Video Generator uses
# (H.264/AAC in MP4/TS/MKV, WAV/raw audio, Rubber Band for pitch/stretch, x264) plus the GPU
# H.264 encoders and decoders for every vendor the platform supports. GPL (x264, Rubber Band).
#
#   build/ffmpeg/build.sh <target> <output folder>
#
# target: linux-x64 (glibc-linked, everything else static), win-x64 (cross-built on Linux with
# mingw-w64, fully static), osx-arm64 or osx-x64 (on a Mac of that kind). Needs C/C++ compilers, make, nasm, meson,
# ninja, cmake, pkg-config and curl (and patchelf for linux-x64). Sources are pinned by SHA-256 below.
#
# GPU support (each loads the vendor's driver at run time, so nothing GPU-specific is bundled):
#   linux-x64: NVIDIA NVENC/NVDEC, AMD AMF, Intel Quick Sync (oneVPL), VAAPI (AMD/Intel), Vulkan
#   win-x64:   NVIDIA NVENC/NVDEC, AMD AMF, Intel Quick Sync (oneVPL), D3D11/DXVA2 decoding, Vulkan
#   osx-arm64, osx-x64: VideoToolbox
set -euo pipefail

target=${1:?target}
out=${2:?output folder}
here=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$here/../.." && pwd)
sources=${FFMPEG_SOURCES:-$root/artifacts/ffmpeg-sources}
work=${FFMPEG_WORK:-$root/artifacts/ffmpeg-build/$target}
jobs=$(getconf _NPROCESSORS_ONLN 2>/dev/null || sysctl -n hw.ncpu)

# name|url|sha256
packages=(
  "ffmpeg|https://github.com/FFmpeg/FFmpeg/archive/refs/tags/n9.0.2.tar.gz|6e374ed621e48faa40639307dff48ba6fe574a509977956d2cce9669b7cc27e9"
  "x264|https://code.videolan.org/videolan/x264/-/archive/b35605ace3ddf7c1a5d67a2eb553f034aef41d55/x264-b35605ace3ddf7c1a5d67a2eb553f034aef41d55.tar.bz2|6eeb82934e69fd51e043bd8c5b0d152839638d1ce7aa4eea65a3fedcf83ff224"
  "rubberband|https://breakfastquay.com/files/releases/rubberband-4.0.0.tar.bz2|af050313ee63bc18b35b2e064e5dce05b276aaf6d1aa2b8a82ced1fe2f8028e9"
)
gpu_packages=(
  "nvcodec|https://github.com/FFmpeg/nv-codec-headers/releases/download/n13.0.19.0/nv-codec-headers-13.0.19.0.tar.gz|13da39edb3a40ed9713ae390ca89faa2f1202c9dda869ef306a8d4383e242bee"
  "amf|https://github.com/GPUOpen-LibrariesAndSDKs/AMF/releases/download/v1.5.3/AMF-headers-v1.5.3.tar.gz|0f521ad98041f8001147cded4a660127f18b1c27a801a5cd691bda1fa48381be"
  "libvpl|https://github.com/intel/libvpl/archive/refs/tags/v2.17.0.tar.gz|4de3e2faf1e8307fb282e4a43f443191810f6a6b0a484fffa7995ba1c814c6ec"
  "vulkan|https://github.com/KhronosGroup/Vulkan-Headers/archive/refs/tags/vulkan-sdk-1.4.363.0.tar.gz|4a078be12bef21cfebc09d878b77a63cff9d68f899254a0b00d0e37ef73e7f7e"
)
linux_packages=(
  "libdrm|https://dri.freedesktop.org/libdrm/libdrm-2.4.134.tar.xz|ac5e74d157830eb8bee44c6a6bf3ad49774ef0dd2a72bdad74a8f20308b52a95"
  "libva|https://github.com/intel/libva/archive/refs/tags/2.24.1.tar.gz|0b4a3649ee8d683b9cce2ef094df4fb039d276c0cef7e49337c43d3b297b9f42"
)

sha256() { if command -v sha256sum >/dev/null; then sha256sum "$1" | cut -d' ' -f1; else shasum -a 256 "$1" | cut -d' ' -f1; fi; }
list() { local IFS=,; echo "$*"; }

prefix="$work/prefix"
cross="" exe="" meson_cross=() cmake_cross=() x264_host=()
case "$target" in
  linux-x64)
    arch=x86_64; os=linux; cc=gcc; cxx=g++
    cflags="-O2 -fPIC"; ldflags="-static-libgcc -static-libstdc++"
    selected=("${packages[@]}" "${gpu_packages[@]}" "${linux_packages[@]}") ;;
  win-x64)
    arch=x86_64; os=mingw32; cross=x86_64-w64-mingw32-; cc=${cross}gcc; cxx=${cross}g++; exe=.exe
    cflags="-O2"; ldflags="-static"
    x264_host=(--host=x86_64-w64-mingw32 --cross-prefix="$cross")
    selected=("${packages[@]}" "${gpu_packages[@]}") ;;
  osx-arm64|osx-x64)
    [ "$target" = osx-arm64 ] && arch=arm64 || arch=x86_64
    os=darwin; cc=clang; cxx=clang++
    cflags="-O2 -arch $arch -mmacosx-version-min=11.0"; ldflags="-arch $arch -mmacosx-version-min=11.0"
    selected=("${packages[@]}") ;;
  *) echo "unknown target $target" >&2; exit 2 ;;
esac

mkdir -p "$sources" "$work" "$out" "$prefix/include" "$prefix/lib/pkgconfig"
export PKG_CONFIG_PATH="$prefix/lib/pkgconfig:$prefix/lib/x86_64-linux-gnu/pkgconfig" PKG_CONFIG_LIBDIR="$prefix/lib/pkgconfig:$prefix/lib/x86_64-linux-gnu/pkgconfig"
export CC="$cc" CXX="$cxx" CFLAGS="$cflags" CXXFLAGS="$cflags" LDFLAGS="$ldflags"

if [ "$target" = win-x64 ]; then
  cat > "$work/meson-cross.txt" <<EOF
[binaries]
c = '${cross}gcc'
cpp = '${cross}g++'
ar = '${cross}ar'
strip = '${cross}strip'
pkg-config = 'pkg-config'
windres = '${cross}windres'
[host_machine]
system = 'windows'
cpu_family = 'x86_64'
cpu = 'x86_64'
endian = 'little'
EOF
  meson_cross=(--cross-file "$work/meson-cross.txt")
  cmake_cross=(-DCMAKE_SYSTEM_NAME=Windows -DCMAKE_C_COMPILER="${cross}gcc" -DCMAKE_CXX_COMPILER="${cross}g++"
               -DCMAKE_RC_COMPILER="${cross}windres" -DCMAKE_FIND_ROOT_PATH_MODE_PROGRAM=NEVER)
fi

for p in "${selected[@]}"; do
  IFS='|' read -r name url sum <<<"$p"
  file="$sources/$name-$(basename "$url")"
  if [ ! -f "$file" ]; then curl -fsSL --retry 3 -o "$file.part" "$url"; mv "$file.part" "$file"; fi
  [ "$(sha256 "$file")" = "$sum" ] || { echo "checksum mismatch: $file" >&2; rm -f "$file"; exit 1; }
  rm -rf "$work/$name"; mkdir -p "$work/$name"
  tar -xf "$file" -C "$work/$name" --strip-components=1
done
has() { [ -d "$work/$1" ]; }
quiet() { "$@" >"$work/last.log" 2>&1 || { tail -60 "$work/last.log" >&2; exit 1; }; }

echo "x264"
( cd "$work/x264"
  quiet ./configure ${x264_host[@]+"${x264_host[@]}"} --prefix="$prefix" --enable-static --disable-cli --enable-pic \
    --disable-opencl --disable-lavf --disable-swscale --disable-ffms --disable-gpac --disable-lsmash \
    ${X264_EXTRA:-} --extra-cflags="$cflags" --extra-ldflags="$ldflags"
  quiet make -j"$jobs"; quiet make install-lib-static )

echo "Rubber Band"
( cd "$work/rubberband"
  # 4.0.0's mathmisc.cpp uses size_t without <cstddef>, which newer libc++ (Xcode 26) rejects.
  export CXXFLAGS="$CXXFLAGS -include cstddef"
  quiet meson setup build ${meson_cross[@]+"${meson_cross[@]}"} --prefix="$prefix" --libdir=lib --buildtype=release \
    --default-library=static -Dfft=builtin -Dresampler=builtin -Djni=disabled -Dladspa=disabled -Dlv2=disabled \
    -Dvamp=disabled -Dcmdline=disabled -Dtests=disabled
  quiet ninja -C build install )

if has nvcodec; then
  echo "NVIDIA headers"
  ( cd "$work/nvcodec"; quiet make PREFIX="$prefix" install )
  echo "AMF headers"
  mkdir -p "$prefix/include/AMF"; cp -R "$work/amf/AMF/." "$prefix/include/AMF/"
  echo "Vulkan headers"
  cp -R "$work/vulkan/include/." "$prefix/include/"
  echo "oneVPL"
  ( cd "$work/libvpl"
    quiet cmake -B build -G Ninja ${cmake_cross[@]+"${cmake_cross[@]}"} -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_INSTALL_PREFIX="$prefix" -DCMAKE_INSTALL_LIBDIR=lib -DBUILD_SHARED_LIBS=OFF \
      -DBUILD_TOOLS=OFF -DBUILD_EXAMPLES=OFF -DBUILD_TESTS=OFF -DINSTALL_EXAMPLES=OFF -DCMAKE_POSITION_INDEPENDENT_CODE=ON
    quiet cmake --build build; quiet cmake --install build )
fi

if has libva; then
  # libva only builds as a shared library, so libva and libdrm ship next to ffmpeg (found via $ORIGIN).
  echo "libdrm"
  ( cd "$work/libdrm"
    quiet meson setup build --prefix="$prefix" --libdir=lib --buildtype=release --default-library=shared \
      -Dintel=disabled -Dradeon=disabled -Damdgpu=disabled -Dnouveau=disabled -Dvmwgfx=disabled \
      -Domap=disabled -Dexynos=disabled -Dfreedreno=disabled -Dtegra=disabled -Dvc4=disabled -Detnaviv=disabled \
      -Dvalgrind=disabled -Dcairo-tests=disabled -Dman-pages=disabled -Dtests=false -Dudev=false
    quiet ninja -C build install )
  echo "libva"
  # Look for VA drivers wherever distributions (and the Flatpak runtime) install them.
  drivers=/usr/lib/x86_64-linux-gnu/dri:/usr/lib64/dri:/usr/lib/dri:/usr/local/lib/dri:/usr/lib/x86_64-linux-gnu/GL/default/lib/dri
  ( cd "$work/libva"
    quiet meson setup build --prefix="$prefix" --libdir=lib --buildtype=release --default-library=shared \
      -Ddriverdir="$drivers" -Dwith_x11=no -Dwith_glx=no -Dwith_wayland=no -Denable_docs=false
    quiet ninja -C build install )
fi

# Only what the app uses. Twitch VODs are H.264 or HEVC with AAC; clips and songs are H.264/AAC MP4.
protocols=(file pipe fd)
demuxers=(mov matroska mpegts flv aac mp3 wav rawvideo pcm_f32le pcm_s16le)
decoders=(h264 hevc aac mp3float mp3 pcm_s16le pcm_s24le pcm_f32le rawvideo wrapped_avframe)
encoders=(libx264 aac pcm_s16le pcm_f32le rawvideo wrapped_avframe)
muxers=(mp4 mov matroska wav pcm_f32le rawvideo null)
parsers=(h264 hevc aac mpegaudio)
bsfs=(aac_adtstoasc h264_mp4toannexb hevc_mp4toannexb extract_extradata null)
filters=(scale pad fps format setsar hwupload rubberband apad atrim afade aformat aresample anull null color sine
  trim setpts asetpts split asplit)
hw=() hwaccels=() threads=() crossflags=() extra_libs=""
case "$target" in
  linux-x64)
    encoders+=(h264_nvenc h264_amf h264_qsv h264_vaapi h264_vulkan)
    hwaccels=(h264_nvdec hevc_nvdec h264_vaapi hevc_vaapi h264_vulkan hevc_vulkan)
    hw=(--enable-ffnvcodec --enable-nvenc --enable-nvdec --enable-amf --enable-libvpl --enable-vaapi --enable-libdrm --enable-vulkan)
    extra_libs="-lstdc++ -ldl -lpthread -lm" ;;
  win-x64)
    encoders+=(h264_nvenc h264_amf h264_qsv h264_vulkan)
    hwaccels=(h264_nvdec hevc_nvdec h264_d3d11va h264_d3d11va2 hevc_d3d11va hevc_d3d11va2 h264_dxva2 hevc_dxva2 h264_vulkan hevc_vulkan)
    hw=(--enable-ffnvcodec --enable-nvenc --enable-nvdec --enable-amf --enable-libvpl --enable-d3d11va --enable-dxva2 --enable-vulkan)
    threads=(--enable-w32threads); crossflags=(--enable-cross-compile --cross-prefix="$cross")
    extra_libs="-lstdc++" ;;
  osx-*)
    encoders+=(h264_videotoolbox)
    hwaccels=(h264_videotoolbox hevc_videotoolbox)
    hw=(--enable-videotoolbox)
    extra_libs="-lc++" ;;
esac

config=(--prefix="$prefix" --arch="$arch" --target-os="$os" --cc="$cc" --cxx="$cxx"
  ${crossflags[@]+"${crossflags[@]}"} ${threads[@]+"${threads[@]}"}
  --pkg-config=pkg-config --pkg-config-flags=--static
  --extra-cflags="-I$prefix/include $cflags" --extra-ldflags="-L$prefix/lib $ldflags" --extra-libs="$extra_libs"
  --enable-gpl --disable-everything --disable-autodetect --disable-network --disable-doc --disable-debug
  --disable-ffplay --enable-ffmpeg --enable-ffprobe --enable-static --disable-shared
  --enable-avdevice --enable-indev=lavfi
  --enable-libx264 --enable-librubberband ${hw[@]+"${hw[@]}"} ${FFMPEG_EXTRA:-}
  --enable-protocol="$(list "${protocols[@]}")" --enable-demuxer="$(list "${demuxers[@]}")"
  --enable-decoder="$(list "${decoders[@]}")" --enable-encoder="$(list "${encoders[@]}")"
  --enable-muxer="$(list "${muxers[@]}")" --enable-parser="$(list "${parsers[@]}")"
  --enable-bsf="$(list "${bsfs[@]}")" --enable-filter="$(list "${filters[@]}")")
[ ${#hwaccels[@]} -gt 0 ] && config+=(--enable-hwaccel="$(list "${hwaccels[@]}")")

echo "FFmpeg"
( cd "$work/ffmpeg"
  ./configure "${config[@]}" >"$work/configure.log" 2>&1 || { tail -40 "$work/configure.log"; tail -60 ffbuild/config.log; exit 1; } >&2
  # Fail loudly if a GPU feature was silently left out.
  for want in libx264 librubberband ${hw[@]+"${hw[@]}"}; do
    flag=$(echo "${want#--enable-}" | tr 'a-z' 'A-Z')
    grep -q "#define CONFIG_$flag 1" config.h || { echo "configure dropped $want" >&2; exit 1; }
  done
  quiet make -j"$jobs" )

cp "$work/ffmpeg/ffmpeg$exe" "$work/ffmpeg/ffprobe$exe" "$out/"
case "$target" in
  osx-*) strip "$out/ffmpeg" "$out/ffprobe" ;;
  *) "${cross}strip" "$out/ffmpeg$exe" "$out/ffprobe$exe" ;;
esac
if [ "$target" = linux-x64 ]; then
  for lib in libva.so.2 libva-drm.so.2 libdrm.so.2; do cp -L "$prefix/lib/$lib" "$out/"; strip "$out/$lib"; done
  # Load those from ffmpeg's own folder (patchelf, because configure mangles '$ORIGIN').
  for f in ffmpeg ffprobe libva.so.2 libva-drm.so.2; do patchelf --set-rpath '$ORIGIN' "$out/$f"; done
  cp "$work/libva/COPYING" "$out/LICENSE-libva.txt"
fi
cp "$work/ffmpeg/COPYING.GPLv2" "$out/LICENSE.txt"
{
  echo "FFmpeg for Burp Music Video Generator ($target), GPL 2 or later (includes x264 and Rubber Band)."
  echo "Built by build/ffmpeg/build.sh in https://github.com/TechDaddyKB/burpMusicVideoGenerator from these sources:"
  for p in "${selected[@]}"; do IFS='|' read -r name url sum <<<"$p"; echo "  $url (sha256 $sum)"; done
  echo "Configure: ${config[*]}"
} > "$out/SOURCES.txt"
echo "ffmpeg for $target in $out"
