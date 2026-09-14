# Okul bilgisayarı pilotu: CST 2025, CPU ve sınırlı disk

Bu pilot üç kapıdan oluşur. İlk iki kapı CST açmaz ve hiçbir simülasyon dosyasını silmez.

1. `rf-sim probe --config C:\RFSimWorker\worker.toml` kurulu CST sürümünü, Python API dizinini ve boş alanı okur.
2. Mock iş hosta bağlanır, küçük sonuç paketini yükler ve hosttaki SHA-256 doğrulamasını sınar. Host ekranında istemci çevrimiçi görünmelidir.
3. Yalnız bundan sonra `gpu=false`, global 32 ağ ve en fazla 20 dakika sınırıyla tek CST 2025 işi çalıştırılır. CST 2026'da üretilmiş bir proje dosyası CST 2025'e geri açılmaya çalışılmaz; model CST 2025 içinde sabit Python kaynakları ve üretilen VBA ile yeniden kurulur.

Mevcut donmuş kaynaklar 2026 API yolunu ve sürüm kaydını sabit içeriyorsa doğrudan çalıştırılmaz. `scripts/prepare-portable-cst-source.py` bunları yeni ve değişmez bir kaynak sürümüne türetir; yerel API yolunu `CST_PYTHON_LIBRARIES`, kaydedilen sürümü `CST_EXPECTED_VERSION` üzerinden alır ve bütün kaynak karmalarını yeniden üretir. `scripts/prepare-cst-runner.ps1` bağdaştırıcı ile yeni manifest karmalarını verir. Bu işlem kaynak uyumluluğunu kanıtlamaz; yanlış 2026 kaydıyla 2025 sonucu yayımlanmasını önler ve gerçek pilotun izlenebilir olmasını sağlar.

İlk CST pilotunda `cleanup_after_upload=false` ve bağdaştırıcıda `--compact` kapalı kalır. Başlangıç ve bitiş boş alanı, CST içi sürüm, model/VBA karmaları, solver dönüşü, karmaşık S-parametreleri ve kapanma kanıtı incelenir.

Pilot geçerse iki aşamalı temizlik açılır:

- Bağdaştırıcının `--compact` seçeneği, doğrulama tamamlandıktan sonra büyük `.cst` çalışma ağacını ve ham mesh dosyalarını kaldırır. `model.vba`, donmuş Python kaynakları, kaynak manifesti, parametreler, karmaşık S-parametreleri, mesh özeti, günlükler, kalite kaydı ve silinen büyük dosyaların karma/boyut bilgileri pakette kalır.
- `cleanup_after_upload=true`, host paketi kabul edip paket karmasını aynen geri bildirdikten sonra istemcideki iş klasörünü ve ZIP'i kaldırır. İstemcide yalnız küçük bir aktarım makbuzu kalır. Yükleme belirsizse veya karma eşleşmezse hiçbir şey silinmez.

29,5 GB boş alan tek küçük pilot için denenebilir; uzun kuyruk için yeterli olduğu kabul edilmez. İstemci, `min_free_gb=15` altına düştüğünde yeni iş kiralamaz. İlk pilotun tepe disk tüketimi ölçüldükten sonra bu eşik ve aynı anda izin verilen iş boyutu yeniden belirlenir.

GPU bir zorunluluk değildir. İstemci GPU gerektirmediğini bildirir ve ilk işler `gpu=false` kullanır. Bu durum sonucu değiştirmemeli, süreyi uzatabilir; sürüm ve solver davranışı gerçek pilot kaydından doğrulanır.
