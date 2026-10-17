@echo off
rem Builds build\dsound.dll (x86) with the installed VS Build Tools.
setlocal
set VCVARS="C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\VC\Auxiliary\Build\vcvars32.bat"
call %VCVARS% >nul || exit /b 1
cd /d "%~dp0"
if not exist build mkdir build
cl /nologo /O2 /W3 /GS- /MT /LD cmfix.c text4k.c /Fo:build\ /Fe:build\dsound.dll /link /DEF:cmfix.def user32.lib kernel32.lib gdi32.lib libcmt.lib /SAFESEH:NO
endlocal
