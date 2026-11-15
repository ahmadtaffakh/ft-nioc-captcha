# FT NIOC uploader and CAPTCHA lab

ابزار `captcha_lab.py` یک رابط محلی برای دریافت CAPTCHA در شبکه شرکت،
برچسب‌گذاری، آموزش مدل RGB ثابت‌طول و آزمون ورود است. نام کاربری و رمز عبور در
فایل یا دیتابیس ذخیره نمی‌شوند.

## نصب

برای آموزش TensorFlow از Python 3.10 تا 3.13 (ترجیحاً 3.12) استفاده کنید:

```powershell
py -3.12 -m venv C:\venvs\ftcaptcha
C:\venvs\ftcaptcha\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
python -m playwright install chromium
```

مسیر venv عمداً کوتاه انتخاب شده است؛ نصب TensorFlow در بعضی مسیرهای بلند
Windows با خطای Long Path متوقف می‌شود.

سپس رابط را اجرا کنید:

```powershell
python captcha_lab.py
```

## جریان کار

1. در شبکه شرکت روی «بازکردن مرورگر» کلیک کنید.
2. مرورگر در تمام عملیات همان اجرای برنامه باز می‌ماند. برای ورود توسط کاربر
   می‌توانید دکمه «ورود دستی» را بزنید.
3. «دریافت CAPTCHA» تصویر اصلی `data:image/...;base64` را می‌خواند؛ اگر
   در دسترس نباشد از screenshot همان عنصر استفاده می‌شود.
4. در دریافت خودکار، «تأخیر بعد از Reload» فاصله زمانی بین بارگذاری صفحه و
   خواندن CAPTCHA بعدی را تعیین می‌کند. مقدار مجاز صفر تا ۶۰ ثانیه است.
5. مقدار درست را با رقم فارسی یا انگلیسی وارد و نمونه را ذخیره کنید.
6. برای ارزیابی اولیه حداقل ۲۰ نمونه لازم است؛ برای استفاده واقعی حداقل
   ۵۰۰ نمونه متنوع توصیه می‌شود.
7. مدل را آموزش دهید و دقت کامل رشته را بررسی کنید.
8. پس از رسیدن به دقت قابل قبول، ورود آزمایشی را با حداقل confidence مناسب
   اجرا کنید. برنامه حداکثر سه بار تلاش می‌کند.

پیام عملیات در کادر خروجی رنگی نمایش داده می‌شود. متن قابل انتخاب است و دکمه
«کپی کل» همه خروجی را برای ارسال گزارش خطا در Clipboard قرار می‌دهد.

## تنظیمات و اطلاعات ورود

تنظیمات غیرحساس مانند Selector، تعداد Batch، تأخیر Reload، Epoch و حداقل
اطمینان در `%LOCALAPPDATA%\ft-nioc-uploader\settings.json` ذخیره می‌شوند.

اگر گزینه «ذخیره امن پس از ورود موفق» فعال باشد، نام کاربری و رمز فقط پس از
ورود خودکار موفق ذخیره می‌شوند. اطلاعات ورود با Windows DPAPI محافظت شده و
به‌صورت متن ساده در فایل تنظیمات یا خروجی برنامه نوشته نمی‌شوند. اطلاعات
محافظت‌شده فقط توسط همان حساب Windows قابل بازیابی است. دکمه «پاک کردن
اطلاعات ورود» فایل Credential را حذف و فیلدهای ورود را خالی می‌کند.

نسخه برنامه در عنوان پنجره و پایین رابط نمایش داده می‌شود.

## ساختار رابط سازمانی

عملیات برنامه در چهار تب مستقل قرار گرفته‌اند:

1. «اتصال و CAPTCHA» برای مرورگر، دریافت دستی و Batch خودکار؛
2. «داده و آموزش» برای پیش‌نمایش، برچسب‌گذاری و آموزش مدل؛
3. «ورود سامانه» برای ورود دستی/خودکار و تنظیمات امن؛
4. «خروجی و گزارش» برای پیام‌های رنگی، انتخاب متن و کپی گزارش.

کنترل‌های اصلی دارای Tooltip هستند. نشانگر ماوس را لحظه‌ای روی دکمه یا فیلد
نگه دارید، یا با صفحه‌کلید روی آن Focus کنید، تا توضیح عملکرد نمایش داده شود.

## جمع‌آوری و برچسب‌گذاری گروهی

1. مرورگر را باز کنید، تعداد Batch را تعیین کنید و «دریافت خودکار» را بزنید.
2. تصاویر و فایل `labels.csv` در پوشه نمایش‌داده‌شده در نوار وضعیت ذخیره می‌شوند.
3. `labels.csv` را با Excel باز کنید و فقط ستون `label` را تکمیل و با همان قالب CSV ذخیره کنید.
4. «ورود Batch برچسب‌خورده» را بزنید و پوشه Batch را انتخاب کنید.

برنامه پیش از ورود، کامل‌بودن همه برچسب‌ها، طول ارقام و وجود تصاویر را بررسی
می‌کند. دکمه «توقف» جمع‌آوری طولانی را پس از عملیات جاری متوقف می‌کند.

داده‌ها و مدل در `%LOCALAPPDATA%\ft-nioc-uploader` نگهداری می‌شوند و نباید
به Git اضافه شوند.

## تست مو‌به‌موی آپلود فایل در اداره

> **مهم:** بسته `FT-Captcha-Portable.zip` فقط آزمایشگاه CAPTCHA را اجرا
> می‌کند و شامل آپلودر پوشه‌ای نیست. برای تست آپلود، فعلاً باید سورس پروژه و
> Python روی سیستم اداره در دسترس باشند.

### ۱. آماده‌سازی اولیه (فقط بار اول)

کل پوشه پروژه را روی سیستم اداره کپی کنید. در مثال زیر فرض شده پروژه در
`C:\FT-Tools\ft.nioc.ir` قرار دارد. اگر آن را جای دیگری کپی کرده‌اید، فقط مقدار
خط اول را عوض کنید.

PowerShell را باز کنید و دستورات زیر را به ترتیب اجرا کنید:

```powershell
$ProjectDir = "C:\FT-Tools\ft.nioc.ir"
Set-Location -LiteralPath $ProjectDir

py -0p
py -3.12 --version
```

اگر دستور دوم نسخه Python 3.12 را نمایش نداد، ابتدا Python 3.12 نسخه 64 بیتی
را نصب کنید و هنگام نصب گزینه `Add python.exe to PATH` را فعال کنید. سپس یک
PowerShell تازه باز کنید و ادامه دهید:

```powershell
$ProjectDir = "C:\FT-Tools\ft.nioc.ir"
Set-Location -LiteralPath $ProjectDir
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass

py -3.12 -m venv C:\venvs\ft-uploader
& C:\venvs\ft-uploader\Scripts\Activate.ps1

python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m playwright install chromium
```

نصب packageها و Chromium به دسترسی اینترنت یا مخزن داخلی مورد تأیید اداره
نیاز دارد. برای اطمینان از نصب صحیح، این کنترل‌ها را اجرا کنید:

```powershell
python --version
python -c "from playwright.sync_api import sync_playwright; print('Playwright OK')"
python -m unittest tests.test_ft_auto_uploader tests.test_ft_browser -v
Test-NetConnection ft.nioc.ir -Port 443
```

نتیجه مورد انتظار:

- عبارت `Playwright OK` چاپ شود.
- هر ۸ تست با وضعیت `OK` تمام شوند.
- در خروجی دستور آخر، مقدار `TcpTestSucceeded` برابر `True` باشد.

این تست‌های محلی فایل واقعی به سامانه FT ارسال نمی‌کنند.

### ۲. آماده‌کردن یک فایل آزمایشی

پوشه تست را ایجاد و خالی کنید:

```powershell
New-Item -ItemType Directory -Path C:\FT-Test -Force
Get-ChildItem -LiteralPath C:\FT-Test -Force
```

اگر پوشه خالی نیست، فایل‌های قبلی را با File Explorer به محل دیگری منتقل
کنید. آپلودر همه فایل‌های موجود در ریشه این پوشه را بررسی می‌کند؛ بنابراین
برای تست اول فقط یک فایل کم‌حجم، غیرمحرمانه و با پسوند مجاز سامانه FT در آن
قرار دهید. نمونه زیر را پس از جایگزین‌کردن مسیر فایل واقعی اجرا کنید:

```powershell
Copy-Item -LiteralPath "C:\Path\To\Allowed-Test-File.pdf" -Destination "C:\FT-Test\Allowed-Test-File.pdf"
Get-ChildItem -LiteralPath C:\FT-Test -File | Select-Object Name, Length, LastWriteTime
```

قبل از ادامه مطمئن شوید فقط همان یک فایل نمایش داده می‌شود.

### ۳. اجرای تست واقعی آپلود

در همان PowerShell این دستورات را اجرا کنید:

```powershell
$ProjectDir = "C:\FT-Tools\ft.nioc.ir"
Set-Location -LiteralPath $ProjectDir
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
& C:\venvs\ft-uploader\Scripts\Activate.ps1

python ft_auto_uploader.py --watch-dir "C:\FT-Test"
```

مرورگر Chromium باز می‌شود. در صفحه FT نام کاربری، رمز عبور و CAPTCHA را
دستی وارد کنید. اطلاعات ورود توسط برنامه در فایل یا دیتابیس ذخیره نمی‌شود.
بعد از ورود، پنجره PowerShell باید پیام زیر را نشان دهد:

```text
پایش پوشه آغاز شد: C:\FT-Test
```

فایل پس از ثابت‌ماندن اندازه آن، آپلود می‌شود و برنامه منتظر نتیجه اسکن سامانه
می‌ماند. پنجره مرورگر و PowerShell را تا مشخص‌شدن نتیجه نبندید.

### ۴. تشخیص نتیجه

- `ارسال و بایگانی شد`: تست موفق بوده و فایل به
  `C:\FT-Test\_uploaded` منتقل شده است.
- `ارسال قبلی تأیید و بایگانی شد`: ارسال قبلی در سامانه تأیید شده است.
- `آپلود شد و در انتظار اسکن است`: آپلود انجام شده، اما اسکن هنوز تمام نشده
  است؛ برنامه را باز نگه دارید.
- `مهلت انتظار اسکن تمام شد`: برنامه را ببندید و دوباره با همان دستور اجرا
  کنید؛ وضعیت قبلی از دیتابیس ادامه داده می‌شود و فایل دوباره آپلود نمی‌شود.
- `rejected` یا `unknown`: برنامه عمداً فایل را به مرحله نهایی نمی‌فرستد. فایل
  را نگه دارید و نتیجه اسکن یا محدودیت نوع فایل را با مسئول سامانه بررسی کنید.
- `خطا برای ...`: متن کامل خطا را همراه با ساعت وقوع نگه دارید و پیش از تلاش
  بعدی بررسی کنید.

برای مشاهده فایل بایگانی‌شده اجرا کنید:

```powershell
Get-ChildItem -LiteralPath C:\FT-Test\_uploaded -File | Select-Object Name, Length, LastWriteTime
```

برای توقف امن برنامه، در پنجره PowerShell کلیدهای `Ctrl+C` را بزنید و منتظر
پیام `پایش متوقف شد.` بمانید. بستن مستقیم پنجره فقط وقتی انجام شود که توقف
عادی پاسخ نمی‌دهد.

### ۵. اجرای تست در دفعات بعد

در دفعات بعد نصب مجدد لازم نیست. فقط پوشه `C:\FT-Test` را بررسی کنید، یک فایل
جدید در ریشه آن بگذارید و اجرا کنید:

```powershell
$ProjectDir = "C:\FT-Tools\ft.nioc.ir"
Set-Location -LiteralPath $ProjectDir
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
& C:\venvs\ft-uploader\Scripts\Activate.ps1
python ft_auto_uploader.py --watch-dir "C:\FT-Test"
```

پروفایل مرورگر و سابقه فایل‌های پردازش‌شده در مسیر زیر نگهداری می‌شوند:

```text
%LOCALAPPDATA%\ft-nioc-uploader
```

این پوشه را بین تست‌ها حذف نکنید؛ حذف آن باعث ازبین‌رفتن نشست مرورگر و سابقه
تشخیص فایل‌های تکراری می‌شود.

## ساخت نسخه Portable کامل

این نسخه TensorFlow و Chromium اختصاصی را همراه خود دارد و روی تین‌کلاینت به
Python، Edge یا نصب package نیاز ندارد. ساخت را روی Windows x64 و Python 3.12
انجام دهید:

```powershell
py -3.12 -m venv C:\venvs\ftcaptcha-build
C:\venvs\ftcaptcha-build\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements-build.txt
powershell -ExecutionPolicy Bypass -File tools\build_portable.ps1
```

خروجی نهایی در `dist\FT-Captcha-Portable.zip` ساخته می‌شود. ZIP را روی
تین‌کلاینت استخراج و `Start-FT-Captcha.bat` یا `FT-Captcha.exe` را اجرا کنید.
آموزش مدل، برچسب‌گذاری، مرورگر و ورود خودکار همگی داخل همین بسته هستند.
