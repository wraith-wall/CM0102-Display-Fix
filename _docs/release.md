# Релиз для игроков

Сборка: `python _tools\release\release.py`. Скрипт собирает cmfix и пишет `_release\cmfix-<версия>.zip` и `cmfix-<версия>.sha256.txt` (хеши каждого файла и архива). Папка `_release` не в git. Версия берётся из `CMFIX_VERSION` в [cmfix.c](../_tools/cmfix/cmfix.c), её же cmfix пишет в первой строке `cmfix.log`.

Архив распаковывается в папку с `cm0102.exe`:

| Файл | Откуда |
|---|---|
| `ddraw.dll` | cnc-ddraw 7.1.0.0 (`_downloads`) |
| `ddraw.ini` | ini cnc-ddraw с ключами `[ddraw]` профиля [release.json](../_tools/wrappers/release.json) |
| `Shaders/omniscale/omniscale.glsl` | из пакета шейдеров cnc-ddraw, лицензия MIT внутри файла |
| `dsound.dll`, `cmfix.ini` | `_tools\cmfix` |
| `README-cmfix.txt` | [_tools/release/README.txt](../_tools/release/README.txt), `{version}` подставляется |
| `licenses/cnc-ddraw-LICENSE.txt` | MIT, из репозитория cnc-ddraw |

Профиль `release` — это `cnc_play_fix` плюс `cmfix.ini` (text4k включается файлом, без переменной окружения) и `width=0`/`height=0` вместо 2400×1800 из dev-профилей (такое окно не влезло бы на экран 1080p). cnc-ddraw тогда открывает окно 800×600, и cmfix один раз при первом появлении окна такого размера увеличивает его до самого большого 4:3, которое помещается в 85% рабочей области монитора (на 4K с рабочей областью 3840×2064 это 2336×1752). Если `width`/`height` в `ddraw.ini` заданы (в `[ddraw]` или `[cm0102]`), cmfix окно не трогает, поэтому dev-профили с 2400×1800 ведут себя как раньше. Тот же набор ставится в dev-папку командой `python _tools\play.py release`.

Перед публикацией не решено и не проверено:

- подгонка первого окна под монитор (код есть, вживую не проверена); ресайз и разворот с `width=0` проверены 2026-10-17;
- установка в чистую копию игры и запуск `cm0102.exe` двойным кликом;
- название мода, лицензия на свой код, страница с исходниками (README ссылается только на хеши);
- проверка архива на VirusTotal.
