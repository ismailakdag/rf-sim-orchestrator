# Okul bilgisayarı pilotu: CST 2025, CPU ve sınırlı disk

## 14 Eylül 2026 mock sonucu

`OKUL-PC-01`, `school-mock-20260914-01` işini 19:09:40 Türkiye saatinde kiraladı. Dokuz artefakt içeren 2.891 baytlık ZIP paketini 19:09:41'de hosta yükledi. Host paketi `e4bab20e700d67df6daf6331e0bf8f01b7b4a0a7bdf234165b84e13a389c5278` SHA-256 karmasıyla kabul etti; aynı paket hosttan yeniden indirildi ve karma tekrar doğrulandı. Temizlik kapalı kaldı ve CST açılmadı. Makine 29,3 GB boş alan bildirdi.

Bağlantı, iş kiralama, sonuç yükleme ve geri indirme kapısı geçti. İstemcinin yetenek kaydında CST kurulumu görünmediği için gerçek CST 2025 işi henüz gönderilmedi. Kayıt `school-pilot-mock-2026-09-14.json` dosyasındadır.

Bu pilot üç kapıdan oluşur. İlk iki kapı CST açmaz ve hiçbir simülasyon dosyasını silmez.

1. `rf-sim probe --config C:\RFSimWorker\worker.toml` kurulu CST sürümünü, Python API dizinini ve boş alanı okur.
2. **İşçiyi başlat** ile çalışan mock iş hosta bağlanır, küçük sonuç paketini yükler ve hosttaki SHA-256 doğrulamasını sınar. **Bağlantıyı test et** yalnız tek seferlik kayıt gönderir ve işi başlatmaz. Host ekranında istemci, işçi döngüsü çalışırken çevrimiçi görünmelidir.
3. Yalnız bundan sonra `gpu=false`, global 32 ağ ve en fazla 20 dakika sınırıyla tek CST 2025 işi çalıştırılır. CST 2026'da üretilmiş bir proje dosyası CST 2025'e geri açılmaya çalışılmaz; model CST 2025 içinde sabit Python kaynakları ve üretilen VBA ile yeniden kurulur.

Mevcut donmuş kaynaklar 2026 API yolunu ve sürüm kaydını sabit içeriyorsa doğrudan çalıştırılmaz. `scripts/prepare-portable-cst-source.py` bunları yeni ve değişmez bir kaynak sürümüne türetir; yerel API yolunu `CST_PYTHON_LIBRARIES`, kaydedilen sürümü `CST_EXPECTED_VERSION` üzerinden alır ve bütün kaynak karmalarını yeniden üretir. `scripts/prepare-cst-runner.ps1` bağdaştırıcı ile yeni manifest karmalarını verir. Bu işlem kaynak uyumluluğunu kanıtlamaz; yanlış 2026 kaydıyla 2025 sonucu yayımlanmasını önler ve gerçek pilotun izlenebilir olmasını sağlar.

İlk CST pilotunda `cleanup_after_upload=false` ve bağdaştırıcıda `--compact` kapalı kalır. Başlangıç ve bitiş boş alanı, CST içi sürüm, model/VBA karmaları, solver dönüşü, karmaşık S-parametreleri ve kapanma kanıtı incelenir.

Pilot geçerse iki aşamalı temizlik açılır:

- Bağdaştırıcının `--compact` seçeneği, doğrulama tamamlandıktan sonra büyük `.cst` çalışma ağacını ve ham mesh dosyalarını kaldırır. `model.vba`, donmuş Python kaynakları, kaynak manifesti, parametreler, karmaşık S-parametreleri, mesh özeti, günlükler, kalite kaydı ve silinen büyük dosyaların karma/boyut bilgileri pakette kalır.
- `cleanup_after_upload=true`, host paketi kabul edip paket karmasını aynen geri bildirdikten sonra istemcideki iş klasörünü ve ZIP'i kaldırır. İstemcide yalnız küçük bir aktarım makbuzu kalır. Yükleme belirsizse veya karma eşleşmezse hiçbir şey silinmez.

29,5 GB boş alan tek küçük pilot için denenebilir; uzun kuyruk için yeterli olduğu kabul edilmez. İstemci, `min_free_gb=15` altına düştüğünde yeni iş kiralamaz. İlk pilotun tepe disk tüketimi ölçüldükten sonra bu eşik ve aynı anda izin verilen iş boyutu yeniden belirlenir.

GPU bir zorunluluk değildir. İstemci GPU gerektirmediğini bildirir ve ilk işler `gpu=false` kullanır. Bu durum sonucu değiştirmemeli, süreyi uzatabilir; sürüm ve solver davranışı gerçek pilot kaydından doğrulanır.

Kurulum taraması hem `Program Files` hem de `Program Files (x86)` altındaki standart CST 2024–2027 yollarını denetler. Sonuç boşsa gerçek CST işi gönderilmez; önce okul bilgisayarındaki `CST DESIGN ENVIRONMENT.exe` ve `AMD64\python_cst_libraries` yolları bulunup `worker.toml` içindeki `cst_roots` değeri düzeltilir.
