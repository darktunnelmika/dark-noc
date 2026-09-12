# DARK NOC v2.9.3 — مرکز فرمان NOC

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
- SSH Workspace کامل xterm.js با Pane نامحدود، چیدمان Grid/Column/Focus، نشست پایدار tmux، اتصال مجدد خودکار، Snippet، ضبط و Replay، تغییر اندازه PTY، جست‌وجو، کلیپ‌بورد، تمام‌صفحه و ذخیره لاگ
- آپلود مستقیم فایل از مرورگر با SFTP، نمایش پیشرفت، جایگزینی اتمیک POSIX روی OpenSSH و مسیر بازیابی‌پذیر برای SFTPهای قدیمی
- انتقال فایل بین دو سرور از مسیر امن Hub، بدون نیاز به دسترسی SSH مستقیم بین آن‌ها
- فایل‌منیجر SFTP با مرور پوشه، ادیتور UTF-8 و ذخیره اتمیک، دانلود، SHA-256، ساخت پوشه، Rename، CHMOD و حذف محافظت‌شده
- نصب HTTPS خودکار با دامنه یا گواهی رمزنگاری‌شده برای IP
- ساخت خودکار نام کاربری و رمز اولیه قوی
- ابزار سروری `darknoc` برای نمایش و تغییر یوزرنیم/رمز، پورت عمومی، دامنه، SSL، وضعیت، لاگ، بکاپ و آپدیت
- اجرای واقعی HTTPS روی پورت عمومی انتخابی با پیش‌فرض `9090` و جداسازی خودکار پورت داخلی Hub
- نصب جداگانه Hub و Node و حفظ تونل‌های قبلی سرور
- Fleet Operations برای Diagnostics، تست تونل، لاگ، کنترل سرویس، Auto-Heal، Sync Agent و ارتقای canary-first همراه با تأیید دقیق نسخه روی چند نود
- Rollup ساعتی متریک‌ها، Retention قابل تنظیم و قفل رهبر برای اجرای امن Controller در چند Process
- Operations Cockpit هر تونل با Health Score، ترافیک، Session، Uptime، Latency/Loss، تاریخچه ۲۴ ساعته و Timeline عملیات
- توپولوژی درختی متحرک با مسیرهای سبز/زرد/خاکستری/قرمز، نمایش جریان ترافیک، جزئیات Hover و ورود مستقیم به مدیریت تونل

در نسخه فعلی افزونه‌های **DARK Backhaul**، **DARK Ghost Pro**، **DARK Packet Pro** و **DARK Realm Pro** در بخش تونل ارائه می‌شوند. Packet Pro جهت واقعی متفاوتی دارد: ایران Client و خارج Server است. Realm نیز Direct Relay بومی است و با نقش Iran Edge و Kharej Gateway، کد `DR1` و ترنسپورت‌های TCP/TLS/WS/WSS کار می‌کند.

بخش **TLS Vault** دامنه را با IP نودی که TLS را خاتمه می‌دهد تطبیق می‌دهد و گواهی Let's Encrypt را توسط Agent همان سرور دریافت می‌کند. برای Backhaul/Ghost معمولاً گواهی روی Listener ایران است؛ برای Realm TLS/WSS گواهی روی Kharej Gateway قرار می‌گیرد. تمدید ۳۰ روز مانده به انقضا خودکار است و کلید خصوصی هرگز به مرورگر یا Pair Code ارسال نمی‌شود.

## نصب Hub

فایل `DARK-NOC-HUB-v2.9.3.tar.gz` را روی سرور مرکزی قرار دهید:

```bash
tar -xzf DARK-NOC-HUB-v2.9.3.tar.gz
cd dark-noc-pro
chmod +x *.sh
sudo bash install-hub.sh
```

نصاب دامنه یا IP و **پورت عمومی پنل** را می‌پرسد. مقدار پیش‌فرض نصب تازه `9090`
است و پنل واقعاً با `https://DOMAIN:9090` یا `https://IP:9090` باز می‌شود. فقط
اگر پورت `443` انتخاب شود، پورت از انتهای آدرس حذف می‌شود. پورت داخلی Hub خودکار
انتخاب می‌شود، فقط روی `127.0.0.1` می‌ماند و در فایروال باز نمی‌شود.

یوزرنیم، رمز، آدرس، پورت و SSL عمداً داخل پنل وب قابل تغییر نیستند. روی سرور Hub
با SSH دستور زیر را اجرا کنید:

```bash
sudo darknoc
```

این منو نمایش اطلاعات ورود، تغییر یا ساخت رمز قوی، تغییر یوزرنیم، تغییر پورت، ثبت
دامنه/IP، دریافت و تمدید Let's Encrypt، وضعیت سرویس‌ها، لاگ، بکاپ و آپدیت را انجام
می‌دهد. اگر فعال‌سازی تنظیم جدید شکست بخورد، تنظیم قبلی برگردانده می‌شود.

## نصب Node

فایل `DARK-NOC-NODE-v2.9.3.tar.gz` را روی Node اجرا کنید:

```bash
tar -xzf DARK-NOC-NODE-v2.9.3.tar.gz
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
