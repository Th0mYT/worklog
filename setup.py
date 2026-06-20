from setuptools import setup

APP = ['worklog_launcher.py']
DATA_FILES = []
OPTIONS = {
    'iconfile': 'assets/worklog.icns',
    'argv_emulation': False,
    'packages': [
        'webview',
        'pynput',
        'logger',
        'summarizer',
        'ui',
        # PyObjC framework packages — must be in 'packages' so their __init__.py
        # is bundled; py2app only copies .so files for C-extension packages otherwise.
        'objc',
        'Quartz',
        'AppKit',
        'Foundation',
        'Cocoa',
        'HIServices',
    ],
    'includes': [
        'config',
        'tomllib',
        # webview runtime dependencies not auto-detected by py2app static analysis
        'proxy_tools',
        'bottle',
        'six',
        'typing_extensions',
    ],
    'plist': {
        'CFBundleName': 'worklog',
        'CFBundleDisplayName': 'worklog',
        'CFBundleIdentifier': 'com.worklog.app',
        'CFBundleVersion': '0.1.0',
        'CFBundleShortVersionString': '0.1.0',
        'NSHighResolutionCapable': True,
        'NSRequiresAquaSystemAppearance': False,
        'NSAccessibilityUsageDescription':
            'worklog needs Accessibility access to track keyboard and mouse activity.',
        'NSScreenRecordingUsageDescription':
            'worklog needs Screen Recording access to read active browser tab URLs.',
    },
    'excludes': ['tkinter', 'matplotlib', 'numpy', 'scipy', 'PIL'],
}

setup(
    name='worklog',
    app=APP,
    data_files=DATA_FILES,
    options={'py2app': OPTIONS},
)