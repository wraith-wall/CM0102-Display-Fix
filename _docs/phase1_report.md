# CM 01/02 на 4K — Phase 1: диагностика и визуальный test harness

Дата: 2026-10-16. Папка: `C:\Games\CM0102_dev` (основная установка `C:\Games\CM0102` не затрагивалась).

## TL;DR

- **`cm0102.exe` рендерит через DirectDraw (IDirectDraw v1, 16 bpp)**. Уверенность высокая: импортируется только `DDRAW.dll!DirectDrawCreate`, в бинарнике нет IID для IDirectDraw2/4/7 и Direct3D, а дизассемблер показывает вызовы `SetCooperativeLevel` / `SetDisplayMode(w,h,16)` / `CreateSurface(PRIMARY)` по vtable-смещениям IDirectDraw v1.
- **На этой системе родной оконный режим `cm0102.exe` не работает.** В оконном режиме игра делает `SetDisplayMode(ширина_десктопа, высота_десктопа, 16)`. Драйвер даёт только 32 bpp, и вызов возвращает `E_NOTIMPL`, после чего появляется окно «Unable to initialise the graphics in windowed mode…». После первых сбоев Windows (Program Compatibility Assistant) **сама** повесила на exe слой `DWM8And16BitMitigation`. С ним игра запускается и крутит главный цикл, но окно остаётся **чёрным**.
- В папке есть официальный **`cm0102_GDI.exe`** из того же патча 3.9.68 (собран 21.11.2002, 17:19, через 4 часа после DirectDraw-сборки). Он не использует DirectDraw, рисует через `StretchDIBits` и **работает**. Baseline снят на нём.
- Обе сборки **DPI-unaware**: манифеста нет. При 200% Windows растягивает окно 800×600 до **1600×1200 физических пикселей** (DWM bitmap-stretch). На 200% это ровно 2× и фактически nearest-neighbour: картинка **не размыта, но блочная**, окно занимает 42% ширины экрана.
- Harness готов. Одна команда запускает игру, проходит путь «меню → настройки → новая игра → состав Arsenal», делает снимки и закрывает игру:
  ```
  python C:\Games\CM0102_dev\_tools\scenario.py C:\Games\CM0102_dev\_tools\scenarios\baseline_gdi.json
  ```
  Прогнан дважды подряд, оба раза ok, 9/9 снимков, все проверены глазами.
- **Рекомендация для Phase 2:** сначала cnc-ddraw (подробности в §6).

---

## 1. Окружение

| | |
|---|---|
| ОС | Windows 11 Pro 25H2, 10.0.26200.9711 |
| Мониторы | 1 шт.: `\\.\DISPLAY1`, 3840×2160 @ 144 Гц, 32 bpp, primary |
| Масштабирование | **200%** (effective DPI 192) |
| GPU / драйвер | NVIDIA GeForce RTX 5070 Ti, драйвер 32.0.16.1742 (30.09.2026) |
| Доступные режимы дисплея | **только 32 bpp** (в том числе с `EDS_RAWMODE`); 800×600×32 есть, 16-битных нет |
| Python | 3.12.10, 64-bit (`C:\Users\<user>\AppData\Local\Programs\Python\Python312`) |
| pip-пакеты | pefile, pywin32, mss, Pillow, capstone, psutil |
| CD | образ смонтирован как `E:` (метка `CM0102`); проверка CD проходит (игра пишет «CD Not Required») |
| Права | обычный пользователь, Medium IL, UAC включён |

## 2. Статический анализ

Полные дампы: [appendix/pe_cm0102.md](appendix/pe_cm0102.md), [appendix/pe_cm0102_GDI.md](appendix/pe_cm0102_GDI.md), дизассемблер инициализации DirectDraw: [appendix/ddraw_callers.txt](appendix/ddraw_callers.txt).

### 2.1 PE

| | cm0102.exe | cm0102_GDI.exe |
|---|---|---|
| Формат | PE32, x86 (32-bit) | PE32, x86 |
| Subsystem | Windows GUI | Windows GUI |
| Linker | 6.0 (MSVC 6), OS version 4.0 | 6.0 |
| TimeDateStamp | 2002-11-21 13:12:05 UTC | 2002-11-21 17:19:18 UTC |
| DllCharacteristics | 0 (нет ASLR/DEP/NX-флагов) | 0 |
| Ресурсы | только cursor/icon | только cursor/icon |
| **Манифест** | **нет**, значит DPI-unaware, нет `dpiAware`, работает UAC-виртуализация | нет |

### 2.2 Импорты (cm0102.exe)

- **DDRAW.dll**: `DirectDrawCreate` (единственная функция; `DirectDrawCreateEx` не импортируется)
- **DSOUND.dll**: ординал 1 (= `DirectSoundCreate`)
- **USER32.dll** (27): `CreateWindowExA`, `RegisterClassA`, `SetWindowLongA`, `MoveWindow`, `GetWindowRect`, `GetDesktopWindow`, `ChangeDisplaySettingsA`, `GetSystemMetrics`, `ShowCursor`/`SetCursor`/`LoadCursorA`, `PeekMessageA`/`GetMessageA`/`DispatchMessageA`, `BeginPaint`/`EndPaint`, `MessageBoxA`…
- **GDI32.dll** (10): только шрифты/текст и печать (`CreateFontA`, `TextOutA`, `GetTextMetricsA`, `StartDocA`…), блитов нет
- **KERNEL32.dll** (84): CRT, файлы, `GetDriveTypeA`/`GetLogicalDriveStringsA` (поиск CD), `SetCurrentDirectoryA`…
- **ADVAPI32.dll**: `RegOpenKeyExA`, `RegQueryValueExA`
- **WSOCK32.dll** (18): сетевая игра
- **WINSPOOL.DRV** / **comdlg32.dll**: печать
- **Нет:** d3d*, dinput (ввод через оконные сообщения), winmm, opengl.

`cm0102_GDI.exe`: то же самое, но **без DDRAW.dll**. Дополнительно есть `GDI32!StretchDIBits`, `SetStretchBltMode`, `SetMapMode` и `USER32!GetDC`/`ReleaseDC`/`SetWindowPos`.

### 2.3 Прочие DLL в папке

| Файл | Что это |
|---|---|
| `Data\Cpuinf32.dll` | Intel-подобная библиотека определения CPU (`wincpuid`, `cpuspeed`, `ProcessorCount`…), импортирует только kernel32/advapi32 |
| `Editor\ZipDll.dll` | «BCB/Delphi Zip» 1.6.0, для редактора |
| `Editor\cm0102ed.exe` | официальный редактор SI, v1.1.1.3874 (Delphi/BCB, GDI) |

Ни одна из них к графике не относится. `ddraw.dll` в папке нет, значит игра берёт системную `C:\Windows\SysWOW64\ddraw.dll`. Это хорошая новость для proxy/wrapper: DLL из папки exe имеет приоритет, и её можно подложить.

### 2.4 Какая версия DirectDraw

- В бинарнике **нет ни одного** IID: ни IDirectDraw2/4/7, ни IDirectDrawSurface2–7, ни IDirect3D. Значит, `QueryInterface` к новым интерфейсам не выполняется, используется **IDirectDraw v1** из `DirectDrawCreate`.
- Функция инициализации графики `0x5E2780` (единственный вызов `DirectDrawCreate`, через thunk `0x944C1C`):
  - **Оконный режим** (флаг `-windowed`, либо Screen Mode = Windowed в game.cfg):
    `GetWindowRect(GetDesktopWindow())`, `SetWindowLongA(GWL_STYLE, 0x00CA0000)` (caption+sysmenu+minimize, **без resize**), затем
    `IDirectDraw::SetCooperativeLevel(hwnd, DDSCL_NORMAL=8)` [vtbl+0x50] и
    `IDirectDraw::SetDisplayMode(desktop_w, desktop_h, **16**)` [vtbl+0x54, 3 аргумента, то есть v1]. Ошибка ведёт к строке «Unable to initialise the graphics in windowed mode … desktop … in 16 bit colour».
  - **Полноэкранный режим:** `SetCooperativeLevel(hwnd, 0x13 = FULLSCREEN|ALLOWREBOOT|EXCLUSIVE)` и `SetDisplayMode(800, 600, 16)`.
  - Затем `CreateSurface({dwFlags=DDSD_CAPS, ddsCaps=DDSCAPS_PRIMARYSURFACE})` [vtbl+0x18]; при флаге 2 создаются дополнительные поверхности.
- Строки: `-windowed`, `-nosound`, `-defaultfont`, `nomemcheck`, `game.cfg`, `CM3_PREFS`, «Screen Mode: Full / Windowed», «… 800 x 600 pixel resolution in 16 bit colour».

**Вывод:** игра рендерит через **DirectDraw 1 (IDirectDraw), 16-bit RGB, primary surface**, в окне или exclusive fullscreen 800×600. Уверенность высокая (импорт + отсутствие IID + дизассемблер + поведение в рантайме).

### 2.5 Важная находка: chdir в основную установку

`WinMain` (`0x68D370`) открывает `HKLM\SOFTWARE\Sports Interactive Ltd\Championship Manager 01-02` с `KEY_ALL_ACCESS`, читает `Location` (= `C:\Games\CM0102`) и делает **`SetCurrentDirectory`** туда (лог: «WinMain() : CWD changed to %s»). Значит, запущенная как есть dev-копия работала бы с файлами основной установки: game.cfg, сейвы, tmp. `game.cfg` в dev-папке тоже содержит путь `C:\Games\CM0102\`, но рабочий каталог он не меняет: по рантайм-проверке открытых файлов всё остаётся в dev.

Как обходит harness, ничего не меняя в exe и реестре: процесс создаётся `CREATE_SUSPENDED`, в его токене выключается UAC-виртуализация реестра (`TokenVirtualizationEnabled=0`), после чего поток возобновляется. Без виртуализации unelevated-процесс не может открыть HKLM с `KEY_ALL_ACCESS`, и игра просто пропускает chdir (это штатная ветка кода). После старта `game.py` проверяет `cwd` и открытые файлы процесса и убивает игру, если видит что-то из `C:\Games\CM0102`. Во всех прогонах `cwd = C:\Games\CM0102_dev`.

## 3. Рантайм-находки по DirectDraw-сборке

1. **Без слоя совместимости** (первые два запуска): окно «Error: Unable to initialise the graphics in windowed mode…» → [baseline/ddraw_01_error_no_compat_layer.png](baseline/ddraw_01_error_no_compat_layer.png).
   Независимая проверка (`_tools\ddraw_probe.py`, тот же порядок вызовов из 64-bit Python): `SetCooperativeLevel(NORMAL)` = S_OK, `SetDisplayMode(3840,2160,16)` = **`E_NOTIMPL`**, а `SetDisplayMode(3840,2160,32)` = S_OK. Режим дисплея при этом не менялся.
   `__COMPAT_LAYER=16BITCOLOR` (только для процесса) не помог.
2. **Windows сама добавила слой совместимости.** После этих сбоев в `HKCU` и `HKLM\…\AppCompatFlags\Layers` появилась запись `C:\Games\CM0102_dev\cm0102.exe = "$ DWM8And16BitMitigation"` (`$` означает, что её создал PCA). Для `C:\Games\CM0102\cm0102.exe` такая запись уже была. **Это постоянное изменение системы, вызванное моими запусками.** Я его не трогал: удаление HKLM-записи требует прав администратора и решать вам.
3. **Со слоем** игра стартует без ошибки, главный цикл крутится на 100% одного ядра. EIP-сэмплинг (`_tools\eip_sample.py`) показал меню-цикл, который опрашивает `quick_start.cfg`. Но **в окне ничего не выводится**: чёрный кадр плюс непрорисованные куски того, что было под окном → [native](baseline/ddraw_02_black_with_pca_layer_native.png), [screen](baseline/ddraw_02_black_with_pca_layer_screen.png). Скрин с экрана (mss) тоже чёрный, то есть дело не в методе захвата.
4. Сразу после создания окна клиентская область DirectDraw-сборки 1200×900. Через ~1–3 с игра сама делает `MoveWindow`, и клиент становится 1600×1200.
5. Полноэкранный режим (смена разрешения на 800×600×16) **не проверялся**, чтобы не трогать режим дисплея.

## 4. Baseline (cm0102_GDI.exe, `-windowed`)

Screen Mode в настройках уже стоит **Windowed**; лаунчер на всякий случай передаёт `-windowed`.

Снимки (копии в `_docs\baseline`, оригиналы в `_captures\20261016_195650_994_baseline_gdi\` и повторный прогон в `_captures\20261016_195808_868_baseline_gdi\`):

| Экран | native (как рисует игра) | на экране (физические px) |
|---|---|---|
| Главное меню «Setup Game» | [gdi_01_main_menu_native.png](baseline/gdi_01_main_menu_native.png) | [gdi_01_main_menu_screen.png](baseline/gdi_01_main_menu_screen.png) |
| Состав Arsenal (Squad) | [gdi_02_squad_native.png](baseline/gdi_02_squad_native.png) | [gdi_02_squad_screen.png](baseline/gdi_02_squad_screen.png) |
| Весь путь, 9 кадров | [gdi_baseline_contact_sheet.png](baseline/gdi_baseline_contact_sheet.png) | |

Наблюдения:

- **Внутреннее разрешение:** 800×600. Клиентская область GDI-сборки в логических px **800×626**: кадр 800×600 в левом верхнем углу и **белая полоса 26 px снизу** (артефакт: окно на 26 px выше кадра).
- **На экране:** внешняя рамка 1612×1310, клиент **1600×1252 физических px** = ×2.0 по обеим осям. Окно занимает 42% ширины и 58% высоты экрана 3840×2160 и не ресайзится: стиль `0x14CA0000` без `WS_THICKFRAME`.
- **Как Windows масштабирует:** процесс `PROCESS_DPI_UNAWARE`, окно `UNAWARE` (DPI окна 96 при DPI монитора 192), значит работает DWM bitmap-stretch. Сравнение физического снимка с апскейлами native-кадра: среднее отличие от **nearest ×2 = 0.003**, от bilinear ×2 = 3.85. То есть при ровно 200% растяжение пиксельно-точное: **не мыльно, но «лесенка»** ([zoom](baseline/gdi_zoom_on_screen_2x.png)). При дробном масштабе (125/150/175%) bitmap-stretch будет размытым; это не проверялось.
- **Глюки рендера:** кроме белой полосы не найдено. Перерисовки корректные, фоны меняются (Background Changes: On).
- **Поведение игры, важное для дальнейших фаз:**
  - Игра сама переписывает `game.cfg` при начале новой игры. Путь `C:\Games\CM0102\` в нём остаётся, а в хвост попадают куски строк окружения (неинициализированный буфер). Копия после первого прогона: `_logs\game.cfg.after_first_gdi_run.bin`.
  - Игра создаёт в своей папке `*.lng`, `*.tmp`, `print\`, `search\`, `tactics\`, `training\`. Это её штатное поведение, harness их не удаляет.
  - Сейвы не создавались, настройки не менялись (Cancel).

## 5. Harness: как пользоваться

Всё в `C:\Games\CM0102_dev\_tools` (git-репозиторий в корне dev-папки отслеживает только `_tools\` и `_docs\`). Все скрипты Per-Monitor-V2 DPI aware, поэтому все координаты экрана у них физические.

```
cd C:\Games\CM0102_dev\_tools
python game.py launch [--exe cm0102_GDI.exe] [--args "-windowed"] [--timeout 60] [--compat LAYER]
python game.py status | paths | kill
python capture.py [--name label] [--method auto|printwindow|screen] [--screen-too] [--desktop] [--hwnd H]
python input.py click X Y [--right] [--double] [--map dpi|fit] [--method sendinput|post]
python input.py key ENTER | type "text" | move X Y | map X Y
python scenario.py scenarios\baseline_gdi.json      # полный путь до экрана состава, ~1 мин
python scenario.py scenarios\smoke_gdi.json         # только старт → главное меню
python scenario.py scenarios\baseline_ddraw.json    # документирует поведение DirectDraw-сборки
python refs.py make NAME SRC.png X0 Y0 X1 Y1 | check SRC.png | list
python ddraw_probe.py [--bpp 16]                     # SetDisplayMode как у игры, вне игры
python eip_sample.py [--stack]                       # где крутится главный поток игры
python pe_analyze.py EXE / xrefs.py / callers.py     # статический анализ
```

- **game.py**: запуск с правильным cwd и выключенной виртуализацией реестра (см. §2.5), ожидание главного окна, PID/HWND в `_logs\game_state.json`, проверка путей. До и после запуска делается снимок режима дисплея; если режим изменился, он восстанавливается через `ChangeDisplaySettingsEx(NULL)`, а если восстановить не удалось — abort. `kill` сначала шлёт WM_CLOSE, затем terminate.
- **capture.py**: по умолчанию `PrintWindow(PW_RENDERFULLCONTENT)`, выполняемый **в DPI-контексте окна игры**. Получается native-кадр 800×600(626) пиксель-в-пиксель. Без этого PrintWindow для unaware-окна кладёт картинку 800×600 в угол чёрного битмапа 1600×1200; это первая найденная проблема. Если кадр пустой, захват откатывается на mss (экран). `--screen-too` дополнительно сохраняет то, что физически на мониторе. Рядом с каждым PNG пишется `.json`: window/client rect, DPI окна и монитора, DPI awareness процесса, масштаб.
- **input.py**: координаты задаются в пространстве игры. `--map dpi` (по умолчанию): пиксель игры = логический пиксель клиента, на экран ×(физ/лог). `--map fit` растягивает `--game-size` на весь клиент (для будущих wrapper'ов). Ввод идёт через `SendInput`. Игра **опрашивает состояние кнопок покадрово**, поэтому:
  - кнопку держим 80 мс (клик в 0 мс игра не видит);
  - курсор остаётся на месте ~0.4 с (кнопки срабатывают на отпускании), затем уводится за пределы клиента. Иначе отпускание может «кликнуть» по только что открывшемуся экрану: так однажды переключилась опция Database;
  - символы печатаются медленно (~120 мс на символ).
  `--method post` (PostMessage) игра **игнорирует**.
- **scenario.py**: JSON со шагами `wait`, `wait_stable`, `wait_match`, `assert_match`, `capture`, `capture_all`, `click` (с `until: "<экран>"`), `key`, `type`, `expect_title`. Результат: `_captures\<stamp>_<name>\` с PNG/JSON, `summary.json` и `contact_sheet.png`. Игра закрывается всегда, даже при ошибке. Код возврата 0/1.
- **refs.py + `_tools\refs\`**: эталоны экранов (заголовки). Сравниваются только яркие пиксели текста, поэтому случайные фоны не мешают: со своим экраном совпадение 1.00, с чужими ≤0.65. Это делает сценарий детерминированным. Проверено: «стабильный кадр» ≠ «готов», потому что загрузка базы замораживает кадр на несколько секунд.

## 6. Проблемы по ходу работы

| Проблема | Решение |
|---|---|
| Игра делает chdir в основную установку | выключение UAC-виртуализации реестра для процесса + проверка cwd/файлов (§2.5) |
| `cm0102.exe` в окне: 16 bpp → `E_NOTIMPL`; со слоем PCA — чёрное окно | baseline снят на `cm0102_GDI.exe`; DirectDraw-сборка — материал для Phase 2 |
| PrintWindow для DPI-unaware окна: картинка в углу | PrintWindow в DPI-контексте окна |
| Ложная «готовность» по стабильному кадру | `wait_match` по эталонам |
| Alt-трюк для SetForegroundWindow переводил окно игры в menu mode и съедал следующий клик | Alt нажимается, только если игра не на переднем плане |
| Двойная обработка клика и игнор 0-мс клика | удержание, задержка перед уводом и увод курсора |
| `-desktop` снимки захватывают всё на экране, включая ваш браузер | в сценариях выключено; 4 таких файла остались в `_captures` (`*_desktop.png`), можно удалить |

## 7. Рекомендация для Phase 2

**Сначала проверить cnc-ddraw**, свою proxy-DLL писать только если он не подойдёт.

Почему:
- Профиль игры — ровно та задача, под которую сделан cnc-ddraw: только `DirectDrawCreate`, IDirectDraw v1, 16-bit primary surface, без Direct3D, ввод через оконные сообщения. cnc-ddraw (по его документации; в этой фазе не проверялось) эмулирует 16 bpp сам, не трогая режим дисплея, рендерит через GDI/OpenGL/D3D9, умеет resizable-окно, сохранение пропорций, nearest/integer или шейдерное масштабирование и маппинг мыши. То есть закрывает все цели (DPI-aware, ресайз, чёткость, мышь), и всё это кладётся рядом с exe как `ddraw.dll` + `ddraw.ini`.
- Своя proxy-DLL реальна: интерфейс маленький, нужны ~10 методов IDirectDraw/Surface/Palette/Clipper. Но это заметная работа: блиты, Lock/Unlock, 16→32 конверсия, презентация, DPI, мышь. Имеет смысл, только если cnc-ddraw не справится, например с WM_* координатами мыши или с этим конкретным порядком вызовов.
- Запасной путь без wrapper'а: `cm0102_GDI.exe`, запущенный как DPI-aware, или с override масштабирования. Его поведение со `StretchDIBits` стоит быстро проверить в Phase 2: если он сам растягивает кадр на клиент, это почти бесплатное решение. Но окно не ресайзится (нет `WS_THICKFRAME`).

Что нужно harness'у для Phase 2:
1. **Управление wrapper'ом в dev-папке**: `game.py` с опцией `--wrapper <dir>`, которая копирует `ddraw.dll`/`ddraw.ini` в папку игры, записывает хэши в лог, а после прогона удаляет их. Плюс матрица конфигов ini (renderer, scaling, maintas, adjmouse…) в сценарии.
2. **Маппинг с letterbox**: режим `--map fit_aspect`, который вычисляет прямоугольник кадра внутри клиента (из ini или детектом чёрных полос) и проверяет точность клика по эталонам `refs`.
3. **Захват**: для окон OpenGL/D3D PrintWindow может вернуть чёрное; fallback на экран уже есть, нужно лишь гарантировать, что окно не перекрыто. Добавить метрики резкости: сравнение физического снимка с nearest/bilinear апскейлом native, как в §4.
4. **Ресайз и разные DPI**: шаги `resize`/`maximize` в сценарии и повтор baseline на нескольких размерах окна.
5. **Слой PCA `DWM8And16BitMitigation`**: он висит на dev-exe и может мешать wrapper'у или маскировать его поведение. Нужно решить (вы), удалить ли его на время Phase 2, или проверять оба состояния. HKLM-запись снимается только с правами администратора.
6. Пройти тот же `baseline_gdi`-путь на `cm0102.exe` + wrapper (эталоны те же, координаты в пространстве игры те же).

---
*Phase 1 закончена, Phase 2 не начинал.*
