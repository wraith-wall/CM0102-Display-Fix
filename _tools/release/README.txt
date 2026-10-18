CM0102 Display Fix {version}
Championship Manager 01/02 on modern screens: sharp scaling up to 4K, crisp
text, working fullscreen and windowed modes. (Technical name: cmfix.)

WHAT IT DOES
- Scales the game's 800x600 picture to your screen with cnc-ddraw (OpenGL,
  OmniScale shader), keeping 4:3 with black bars at the sides. No more tiny
  window or blurry stretching on 1440p/4K monitors.
- The in-game Screen Mode setting (Game Settings) works again: Full = borderless
  fullscreen, Windowed = a resizable window. Alt+Enter switches between them,
  and the window's maximize button works as usual.
- Crisp text: the game's text is redrawn at your screen's resolution instead of
  being scaled up from 800x600. This is for the default "Futuristic" fonts;
  with "Traditional" fonts the game draws its text as before.

REQUIREMENTS
- CM 01/02 patched to 3.9.68 (the final official patch), started through
  cm0102.exe. cm0102_GDI.exe is not supported.
- A graphics card with OpenGL (any reasonably recent one).
- For crisp text: the font "Eras Demi ITC" (installed with Microsoft Office).
  Without it the game still runs scaled, just with the normal pixel text.
- Tested on Windows 11.

INSTALL
1. If the game folder already has ddraw.dll, ddraw.ini or dsound.dll (another
   wrapper such as dgVoodoo), move them somewhere safe first.
2. Copy everything from this archive into the folder that contains cm0102.exe.
3. Start the game as usual.

UNINSTALL
Delete these files from the game folder: ddraw.dll, ddraw.ini, dsound.dll,
cmfix.ini, cmfix.log, Shaders\omniscale\omniscale.glsl (and the Shaders folder
if it is empty), README-CM0102-Display-Fix.txt and the licenses folder.
Without cnc-ddraw the game may not start in windowed mode on modern Windows:
if it shows an error or a black window, set Screen Mode back to Full (or
reinstall cmfix).

SETTINGS
- cmfix.ini: crisp text on/off (text4k=1/0), its font and size.
- ddraw.ini: cnc-ddraw's own settings. Useful ones: width/height (window size
  in windowed mode; 0 = the first window is sized to the largest 4:3 that fits
  your screen), shader (scaling filter).

COMPATIBILITY WITH COMMUNITY PATCHES
The exe is not changed on disk; the patching happens in memory when the game
starts, and each hook is only installed if the game code there is the 3.9.68
original. Nick's CM0102Patcher / CM0102Loader patches (including the Saturn
and year-specific sets) were checked: none of them changes the code cmfix
hooks. Resolution patches (1280x800, 1024x576, ...) change the game's frame
size: crisp text then turns itself off. Other combinations are untested.

KNOWN LIMITATIONS
- Some text stays low-res: input fields while you type, text with embedded
  colours, and the arrow icons.
- Big titles use Eras instead of the game's HandelGothic.
- Behind modal dialogs (e.g. Game Settings) the menu text underneath is hidden
  instead of shown dimmed.

TROUBLESHOOTING
- cmfix.log in the game folder says what cmfix did at startup and why a part
  turned itself off.
- Antivirus warnings: dsound.dll is a small proxy that patches the running game
  in memory, which some scanners flag. Compare its SHA256 with the one
  published with the release.

CREDITS AND LICENSES
- cnc-ddraw by FunkyFr3sh, MIT license: licenses\cnc-ddraw-LICENSE.txt
- OmniScale shader by Lior Halphon, MIT license (notice inside the shader file)
