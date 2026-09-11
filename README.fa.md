# DARK NOC v2.6.0 — مرکز فرمان NOC

[English](README.md) · **فارسی** · توسعه‌دهنده و پشتیبانی: **@mikakhadm**

حالت Hybrid Pair Code سمت ایران را از پنل نصب می‌کند و برای سرور خارجی بدون SSH/Agent یک کد سازگار با مسیر عادی KHAREJ اسکریپت DARK Backhaul می‌سازد.

بخش Tunnel Manager نصب بودن Core را خودکار تشخیص می‌دهد و ایجاد، شروع، توقف، ری‌استارت، لاگ، تست، ویرایش پورت/Profile/Transport و حذف هر تونل را مدیریت می‌کند.

DARK NOC یک پنل NOC مستقل برای مانیتورینگ سرورهای لینوکسی، مدیریت رخدادها،
کنترل سرویس‌ها، استقرار هماهنگ DARK Backhaul و اتصال SSH از داخل مرورگر است.

## نصب سریع

روی سرور مرکزی Hub با کاربر root اجرا کنید:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/darktunnelmika/dark-noc/main/install.sh) hub
```

روی هر سرور ایران یا خارج که باید به پنل متصل شود اجرا کنید:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/darktunnelmika/dark-noc/main/install.sh) node
```

نصاب سریع آخرین Release پایدار را دریافت می‌کند، SHA-256 فایل را بررسی می‌کند
و سپس کار را آغاز می‌کند. اگر Hub از قبل نصب باشد، خودکار وارد مسیر ارتقای دارای
Rollback می‌شود؛ روی سرور تازه، نصب تعاملی را اجرا می‌کند.

## امکانات اصلی

- داشبورد زنده CPU، RAM، دیسک، Load، پهنای باند، Uptime و Connectionها
- مانیتورینگ Inode، دما، سرعت I/O دیسک، خطا و Drop شبکه، آپدیت و Reboot سیستم‌عامل و سلامت Docker
- مانیتورهای ICMP، TCP، HTTP، HTTPS، DNS، انقضای TLS و SNMP رمزنگاری‌شده از روی Agent انتخابی
- نمایش IP کنار نام سرورها در Live Tunnel Matrix
- تشخیص خودکار نمونه‌های DARK Backhaul مدیریت‌شده توسط پنل
- ساخت هماهنگ تونل روی سرور ایران و خارج
- پروفایل‌های Stable، Balanced، Low Ping و Turbo
- Retry، حذف دوطرفه و Rollback خودکار در شکست نصب
- Incident Center کامل با Timeline، یادداشت اپراتور، Acknowledge، علت اصلی، راه‌حل، Reopen و Recovery خودکار
- SSH مرورگری با رمزنگاری اطلاعات ورود و Pin شدن Host Key
- ترمینال کامل xterm.js با ANSI/VT، دو نشست هم‌زمان، تغییر اندازه PTY، جست‌وجو، کلیپ‌بورد، تمام‌صفحه و ذخیره لاگ
- آپلود مستقیم فایل از مرورگر با SFTP، نمایش پیشرفت، جایگزینی اتمیک POSIX روی OpenSSH و مسیر بازیابی‌پذیر برای SFTPهای قدیمی
- انتقال فایل بین دو سرور از مسیر امن Hub، بدون نیاز به دسترسی SSH مستقیم بین آن‌ها
- فایل‌منیجر SFTP با مرور پوشه، ادیتور UTF-8 و ذخیره اتمیک، دانلود، SHA-256، ساخت پوشه، Rename، CHMOD و حذف محافظت‌شده
- نصب HTTPS خودکار با دامنه یا گواهی رمزنگاری‌شده برای IP
- ساخت خودکار نام کاربری و رمز اولیه قوی
- نصب جداگانه Hub و Node و حفظ تونل‌های قبلی سرور
- Fleet Operations برای اجرای هماهنگ Diagnostics، تست تونل، لاگ، کنترل سرویس، Auto-Heal و Sync Agent روی چند نود
- Rollup ساعتی متریک‌ها، Retention قابل تنظیم و قفل رهبر برای اجرای امن Controller در چند Process

در نسخه فعلی افزونه‌های **DARK Backhaul**، **DARK Ghost Pro** و **DARK Packet Pro** در بخش تونل ارائه می‌شوند. Packet Pro جهت واقعی متفاوتی دارد: ایران Client و خارج Server است؛ پنل در حالت Pair Code سمت ایران را می‌سازد و کد `DPP-N1` را برای اسکریپت خارج تحویل می‌دهد.

بخش **TLS Vault** دامنه را با IP نود ایران تطبیق می‌دهد، گواهی Let's Encrypt را توسط Agent روی همان سرور دریافت می‌کند و ۳۰ روز مانده به انقضا تمدید را خودکار در صف قرار می‌دهد. کلید خصوصی به مرورگر یا Pair Code ارسال نمی‌شود. هنگام انتخاب ترنسپورت TLS/WSS/H2/gRPC فقط گواهی معتبر همان نود قابل انتخاب است.

## نصب Hub

فایل `DARK-NOC-HUB-v2.6.0.tar.gz` را روی سرور مرکزی قرار دهید:

```bash
tar -xzf DARK-NOC-HUB-v2.6.0.tar.gz
cd dark-noc-pro
chmod +x *.sh
sudo bash install-hub.sh
```

نصاب دامنه یا IP و پورت داخلی Hub را می‌پرسد. با Enter روی پورت، یک پورت آزاد
تصادفی ساخته می‌شود. پنل از بیرون همیشه با `https://DOMAIN` باز می‌شود.

## نصب Node

فایل `DARK-NOC-NODE-v2.6.0.tar.gz` را روی Node اجرا کنید:

```bash
tar -xzf DARK-NOC-NODE-v2.6.0.tar.gz
cd dark-noc-node
chmod +x *.sh
sudo bash install-node.sh
```

نصاب Node هیچ آدرس Hub یا Tokenی نمی‌پرسد؛ فقط پیش‌نیازها و SSH را آماده می‌کند
و تونل‌های قبلی را تغییر نمی‌دهد. بعد از پایان، در پنل **ADD NODE** را بزنید و
IP، پورت SSH و رمز root یا Private Key را وارد کنید. Hub به‌صورت خودکار Agent،
Token خصوصی و TLS را روی Node تنظیم می‌کند و تا دریافت اولین Heartbeat منتظر می‌ماند.
وضعیت در کارت سرور نمایش داده می‌شود؛ برای خطا **INSTALL LOG** و سپس
**RETRY INSTALL** را استفاده کنید.

## ارتقا

```bash
# روی Hub؛ تشخیص نسخه نصب‌شده، دانلود، بررسی Checksum و ارتقای امن
bash <(curl -fsSL https://raw.githubusercontent.com/darktunnelmika/dark-noc/main/install.sh) hub
```

قبل از ارتقای Hub از دیتابیس SQLite نسخه پشتیبان سازگار گرفته می‌شود و در صورت
خطا Rollback انجام خواهد شد. بعد از ارتقای Hub، روی کارت هر Node راه‌دور که SSH
آن تنظیم شده گزینه **SYNC AGENT** را بزنید تا Agent و سرویس امن همان نسخه، بدون
تغییر تنظیمات تونل‌ها و Auto-Heal، دوباره همگام شوند.

## امنیت

- پورت داخلی Hub فقط روی `127.0.0.1` گوش می‌دهد.
- برای Node راه‌دور فقط HTTPS پذیرفته می‌شود.
- Token هر Node محرمانه و مستقل است و توسط Hub ساخته و مستقیم روی Node تنظیم می‌شود.
- کلید یا رمز SSH هیچ‌وقت به مرورگر بازگردانده نمی‌شود.
- گزارش آسیب‌پذیری را عمومی نکنید؛ از بخش Security Advisory گیت‌هاب استفاده کنید.

## پشتیبانی

شناسه رسمی پروژه: **@mikakhadm**

برای گزارش باگ، Version، سیستم‌عامل، مراحل تکرار و Log سانسورشده را ارسال کنید.
هیچ‌وقت Password، Token، Private Key یا اطلاعات کامل سرورها را منتشر نکنید.
