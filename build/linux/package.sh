#!/usr/bin/env bash
# Packages the PyInstaller folder into the Linux downloads: .tar.gz, AppImage, .deb and Flatpak.
#
#   build/linux/package.sh <pyinstaller folder> <version> <edition> <output folder> [formats]
#
# edition: standard or tensorflow. formats: any of "tar appimage deb flatpak" (default: all).
# Needs: dpkg-deb, curl, and for Flatpak flatpak + flatpak-builder (the runtime comes from Flathub).
set -euo pipefail

app=$(cd "${1:?pyinstaller folder}" && pwd)
version=${2:?version}
edition=${3:?edition}
out=$(mkdir -p "${4:?output folder}" && cd "$4" && pwd)
formats=${5:-tar appimage deb flatpak}
here=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$here/../.." && pwd)
id=com.techdaddydigital.BurpMusicVideoGenerator
bin=burp-music-video-generator
suffix=""; [ "$edition" = tensorflow ] && suffix="-tensorflow"
base="burp-music-video-generator-$version-linux-x64$suffix"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

want() { [[ " $formats " == *" $1 "* ]]; }

if want tar; then
  echo "tar.gz"
  mkdir -p "$work/tar/$base"
  cp -a "$app/." "$work/tar/$base/"
  cp "$here/$id.desktop" "$root/assets/icon.png" "$work/tar/$base/"
  cat > "$work/tar/$base/README.txt" <<EOF
Burp Music Video Generator $version ($edition edition)

Run ./$bin to open the app, or ./$bin --help for the command line.
Optional menu entry: copy $id.desktop to ~/.local/share/applications/ and
edit its Exec= line to point at this folder.
EOF
  tar -C "$work/tar" -czf "$out/$base.tar.gz" "$base"
  rm -rf "$work/tar"
fi

if want appimage; then
  echo "AppImage"
  dir="$work/AppDir"
  mkdir -p "$dir/usr/lib/$bin" "$dir/usr/share/applications" "$dir/usr/share/icons/hicolor/512x512/apps"
  cp -a "$app/." "$dir/usr/lib/$bin/"
  cp "$here/$id.desktop" "$dir/$id.desktop"
  cp "$here/$id.desktop" "$dir/usr/share/applications/"
  cp "$root/assets/icon.png" "$dir/$id.png"
  cp "$root/assets/icon.png" "$dir/usr/share/icons/hicolor/512x512/apps/$id.png"
  cat > "$dir/AppRun" <<EOF
#!/bin/sh
here=\$(dirname "\$(readlink -f "\$0")")
exec "\$here/usr/lib/$bin/$bin" "\$@"
EOF
  chmod +x "$dir/AppRun"
  tool="$work/appimagetool"
  curl -fsSL --retry 3 -o "$tool" https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage
  chmod +x "$tool"
  ARCH=x86_64 APPIMAGE_EXTRACT_AND_RUN=1 "$tool" --no-appstream "$dir" "$out/$base.AppImage" >/dev/null
  rm -rf "$dir"
fi

if want deb; then
  echo ".deb"
  package=burp-music-video-generator$suffix
  other=burp-music-video-generator; [ -z "$suffix" ] && other=burp-music-video-generator-tensorflow
  dir="$work/deb"
  mkdir -p "$dir/DEBIAN" "$dir/opt/$package" "$dir/usr/bin" "$dir/usr/share/applications" \
    "$dir/usr/share/icons/hicolor/512x512/apps" "$dir/usr/share/doc/$package"
  cp -a "$app/." "$dir/opt/$package/"
  ln -s "/opt/$package/$bin" "$dir/usr/bin/$bin"
  cp "$here/$id.desktop" "$dir/usr/share/applications/"
  cp "$root/assets/icon.png" "$dir/usr/share/icons/hicolor/512x512/apps/$id.png"
  cp "$root/LICENSE" "$dir/usr/share/doc/$package/copyright"
  size=$(du -sk "$dir" | cut -f1)
  cat > "$dir/DEBIAN/control" <<EOF
Package: $package
Version: $version
Architecture: amd64
Maintainer: Kristopher Barrantes <camarokris@gmail.com>
Installed-Size: $size
Depends: libc6 (>= 2.35)
Conflicts: $other
Replaces: $other
Provides: burp-music-video-generator
Section: video
Priority: optional
Homepage: https://github.com/TechDaddyKB/burpMusicVideoGenerator
Description: Turn a Twitch streamer's burps into a music video
 Downloads a streamer's archived broadcasts, finds burps (or any of 521 sounds)
 and plays a MIDI song with them, pitch-shifted and stretched to fit.
 This is the $edition edition.
EOF
  dpkg-deb --build --root-owner-group -Zxz "$dir" "$out/${package}_${version}_amd64.deb" >/dev/null
  rm -rf "$dir"
fi

if want flatpak; then
  echo "Flatpak"
  dir="$work/flatpak"
  mkdir -p "$dir/files"
  cp -a "$app/." "$dir/files/app/"
  cp "$here/$id.desktop" "$root/assets/icon.png" "$dir/files/"
  cat > "$dir/$id.yml" <<EOF
app-id: $id
runtime: org.freedesktop.Platform
runtime-version: '24.08'
sdk: org.freedesktop.Sdk
command: $bin
finish-args:
  - --share=network        # Twitch search and downloads
  - --share=ipc
  - --socket=x11           # the Tk window (runs through XWayland on Wayland)
  - --device=all           # GPU encoders: /dev/dri for AMD/Intel, /dev/nvidia* for NVENC
  - --filesystem=home      # pick MIDI files and save videos anywhere in your home folder
modules:
  - name: burp-music-video-generator
    buildsystem: simple
    build-commands:
      - mkdir -p /app/lib /app/bin
      - cp -a app /app/lib/$bin
      - ln -s /app/lib/$bin/$bin /app/bin/$bin
      - install -Dm644 $id.desktop /app/share/applications/$id.desktop
      - install -Dm644 icon.png /app/share/icons/hicolor/512x512/apps/$id.png
    sources:
      - type: dir
        path: files
EOF
  ( cd "$dir"
    flatpak-builder --user --install-deps-from=flathub --disable-rofiles-fuse --force-clean \
      --repo=repo build "$id.yml" >flatpak-build.log 2>&1 || { tail -40 flatpak-build.log >&2; exit 1; }
    flatpak build-bundle --runtime-repo=https://flathub.org/repo/flathub.flatpakrepo repo "$out/$base.flatpak" "$id" )
  rm -rf "$dir"
fi

ls -la "$out"
