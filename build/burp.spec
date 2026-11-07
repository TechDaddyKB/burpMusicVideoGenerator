# PyInstaller recipe for the packaged app (see .github/workflows/release.yml).
#
#   EDITION=standard|tensorflow FFMPEG_DIR=<built ffmpeg folder> pyinstaller build/burp.spec
#
# standard:   YAMNet runs on the built-in NumPy copy (small download).
# tensorflow: bundles TensorFlow and Google's YAMNet SavedModel (yamnet_savedmodel/ next to this repo's
#             code, downloaded by the workflow), which can use an NVIDIA GPU on Linux.
import os
import sys
from pathlib import Path

root = Path(SPECPATH).parent
edition = os.environ.get('EDITION', 'standard')
ffmpeg_dir = Path(os.environ['FFMPEG_DIR'])
name = 'burp-music-video-generator'

datas = [
    (str(root/'yamnet_sounds.txt'), '.'),
    (str(root/'yamnet_weights.npz'), '.'),
    (str(root/'assets'/'icon.png'), 'assets'),
    (str(root/'LICENSE'), '.'),
    (str(ffmpeg_dir), 'ffmpeg'),
]
hiddenimports = ['yamnet_numpy']
excludes = ['IPython', 'matplotlib', 'pytest']
if edition == 'tensorflow':
    datas.append((str(root/'yamnet_savedmodel'), 'yamnet_savedmodel'))
    hiddenimports += ['tensorflow']
else:
    excludes += ['tensorflow', 'tf_keras', 'keras', 'tensorboard']

a = Analysis([str(root/'burp_app.py')], pathex=[str(root)], datas=datas, hiddenimports=hiddenimports,
             excludes=excludes, noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name=name, console=sys.platform.startswith('linux'),
          icon=str(root/'assets'/'icon.png'), upx=False)
coll = COLLECT(exe, a.binaries, a.datas, name=name, upx=False)
if sys.platform == 'darwin':
    app = BUNDLE(coll, name='Burp Music Video Generator.app', icon=str(root/'assets'/'icon.png'),
                 bundle_identifier='com.techdaddydigital.burpmusicvideogenerator',
                 info_plist={'NSHighResolutionCapable': True,
                             'CFBundleShortVersionString': os.environ.get('APP_VERSION', '0.0.0')})
