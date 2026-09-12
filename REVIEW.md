# Doğrulama kaydı — 12 Eylül 2026

Bu depo, okul bilgisayarında çalışacak bir Python worker ile ana bilgisayardaki kalıcı
iş kuyruğunu birbirine bağlar. Tailscale zorunlu değildir. Kullanıcının isteği doğrultusunda
okul bilgisayarına bağlanma, kurulum, Wake-on-LAN gönderimi ve gerçek CST çözümü yapılmadı.

İki GPT-5.6 Sol/high ajanının uygulama ve bağımsız incelemesi ana ajan tarafından birleştirildi.
Son yerel test paketi 25 testten oluşur. Ayrı host/worker alt süreçleriyle mock iş, paket
aktarımı ve doğrulanmış indirme; ayrıca gerçek CLI/API üzerinden gerekçeli iş kapatma sınandı.
Mevcut CST iş belgesinin biçimi sahte alt süreçle sınandı; ham arşiv ve modeller korunur.

Sonuç paketi dosya boyutlarını, SHA-256 değerlerini ve tam değişmez iş belgesinin karmasını
taşır. Host, geçerli iş sahipliğini ve son tarihi tekrar denetler. Zaman aşımı veya belirsiz
süreç/bağlantı sonucu otomatik tekrar başlatmaz; worker incelemeye ayrılır. `resolve`, operatörün
etkin solver kalmadığını kontrol edip gerekçe yazmasından sonra işi tekrar çalıştırmadan kapatır.

Yol geçişi, sembolik bağ, Windows reparse point/ADS/aygıt adı, büyük manifest, yinelenen
arşiv girdisi, sonlu olmayan sayı ve boolean-as-integer girişleri test kapsamındadır.

`completed`, paketin aktarım ve dosya bütünlüğü kontrollerinin geçtiğini ifade eder.
Bilimsel geçerlilik, uzaysal yakınsama veya gerçek doku doğruluğu değildir. Bunlar kalite
kayıtlarında ve proje analizinde ayrıca değerlendirilir.

Bu sürümün sınırları: ortak Bearer belirteci için ayrı yetki rolleri yoktur; büyük sonuç
yükleme istemcisi kurumsal proxy kullanmaz. Worker'ın host URL'sine doğrudan ulaşabilmesi
gerekir. Ağ, lisans, CST ve Wake-on-LAN davranışı gerçek okul kurulumu sırasında doğrulanmalıdır.
