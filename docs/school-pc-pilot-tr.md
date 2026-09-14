# Okul bilgisayarı pilotu: CST 2025, CPU ve sınırlı disk

## 14 Eylül 2026 G5 sürüm köprüsü zaman aşımı

`school-g5-bridge-control-v3-20260914` işi 20:57:02 Türkiye saatinde `OKUL-PC-01` tarafından kiralandı. İşçi 21:16:45'e kadar `runner_active` heartbeat'i gönderdi. Sabit çalıştırıcının 1.200 saniyelik sınırı 21:17:04'te aşıldı. Sonuç paketi yüklenmedi; iş `needs_attention` durumuna, işçi de yeni iş alamayan güvenli duruma geçti. İkinci kontrast işi hiç kiralanmadı.

İşçi, üst Python süreci ile PID 4144 ve altındaki CST/solver süreçlerinin birlikte kapandığını kanıtlayamadığı için bunları otomatik sonlandırmadı ve işi otomatik yeniden sıraya almadı. Okul bilgisayarında etkin CST/solver kalıp kalmadığı ve yerel iş günlüğü denetlenmeden yeni iş başlatılmaz. Bu kayıt bilimsel sonuç değildir; süreç yaşam döngüsü ve süre sınırı iyileştirmesi için operasyon kanıtıdır.

Sorunun nedeni, CST içindeki 1.200 saniyelik vaka sınırı ile dış Python gözetmeninin 1.200 saniyelik sınırının aynı anda dolmasıdır. İç betik `abort_solver` çağrısıyla kullanıcı onayı bekleyen Abort penceresini açarken dış gözetmen de belirsiz duruma geçti. `v4` düzeltmesinde okul CPU solver bütçesi 2.700 saniyeye çıkarıldı ve dış gözetmene ek 300 saniye kapanış payı verildi. İşçi ayrıca görünür CST Abort ve License pencerelerini tıklamadan algılayıp heartbeat aşamasında `interaction_required` olarak bildirir. Köprü geçse bile keşfedilen okul hızına göre kalan 40 iş ayrıca serbest bırakılmadan başlamaz.

## 14 Eylül 2026 gerçek CST 2025 pilot sonucu

`school-cst2025-widefield-20260914-165341` işi okul bilgisayarında CST 2025 ve CPU ile tamamlandı. İş 19:54:28 Türkiye saatinde başladı; solver 19:54:36–19:56:49 arasında 132,141 saniye çalıştı ve bütün iş 145 saniye sürdü. Host 19:56:56'da 613 dosyalı, 9.401.720 baytlık paketi `4b212dd3088ad50e6696f9b68ab610215e0276e22915f4f6c2c920b47ca7d64c` SHA-256 karmasıyla doğruladı. Sonuç tekrar indirilip aynı karma ile denetlendi. Enerji kriteri, pasiflik, proje kapanışı ve 4.001 sonlu kompleks örnek denetimleri geçti.

| Denetim | CST 2025 okul bilgisayarı | CST 2026 yerel `b00001` | Sonuç |
| --- | ---: | ---: | --- |
| Solver süresi | 132,141 saniye | 40,000 saniye | Bu örnekte okul bilgisayarı 3,30 kat daha yavaş |
| Frekans ekseni | 1–6 GHz, 4.001 örnek | 1–6 GHz, 4.001 örnek | Birebir aynı |
| Mesh hücresi | 493.148 | 493.148 | Aynı |
| `mesh-grid.bin` karması | `77784e…9017` | `77784e…9017` | Birebir aynı |
| CST içi sentetik küre hacmi | 0,5235987756 mm³ | 0,5235987756 mm³ | Aynı |
| Dört S eğrisi kompleks RMS farkı | 0,0004284934 | — | Yerel eğrilerin RMS genliğinin %0,07244'ü |

Fiziksel geometri VBA'sı, GPU satırı ve CST 2025'te desteklenmeyen örnekleme kuralı satırı çıkarıldığında birebir aynıdır. Buna rağmen iki bilgisayar arasındaki mutlak S-matrisi farkı, yerel `b00001`–`b00002` kontrol–kontrast etkisinin dört eğride 26,81 katı, S21'de 13,70 katıdır. Bu sonuç mutlak eğrilerin bilgisayarlar arasında doğrudan havuzlanamayacağını gösterir. Bilimsel paralel çalışma için her kontrol–kontrast çifti aynı bilgisayar ve CST sürümünde tamamlanmalı; okul bilgisayarında `b00002` karşılığının kısa köprü koşusu yapılarak çift içi farkın CST 2026 çiftiyle eşleşmesi sınanmalıdır. Bu ikinci iş otomatik başlatılmadı.

Makine tarafından okunabilir karşılaştırma `school-pilot-cst2025-result-2026-09-14.json`, yeniden üretilebilir analiz `scripts/analyze-school-cst-bridge.py` içindedir. İlk pilot gereği okul bilgisayarındaki ham CST çalışma ağacı temizlenmedi.

## 14 Eylül 2026 mock sonucu

`OKUL-PC-01`, `school-mock-20260914-01` işini 19:09:40 Türkiye saatinde kiraladı. Dokuz artefakt içeren 2.891 baytlık ZIP paketini 19:09:41'de hosta yükledi. Host paketi `e4bab20e700d67df6daf6331e0bf8f01b7b4a0a7bdf234165b84e13a389c5278` SHA-256 karmasıyla kabul etti; aynı paket hosttan yeniden indirildi ve karma tekrar doğrulandı. Temizlik kapalı kaldı ve CST açılmadı. Makine 29,3 GB boş alan bildirdi.

Bağlantı, iş kiralama, sonuç yükleme ve geri indirme kapısı geçti. İstemcinin ilk yetenek kaydında CST kurulumu görünmediği için gerçek CST 2025 işi gönderilmedi. Sonradan doğrulanan yürütülebilir dosya `C:\Program Files (x86)\CST Studio Suite 2025\CST DESIGN ENVIRONMENT.exe` yolundadır. Kayıt `school-pilot-mock-2026-09-14.json` dosyasındadır.

Bu pilot üç kapıdan oluşur. İlk iki kapı CST açmaz ve hiçbir simülasyon dosyasını silmez.

1. `rf-sim probe --config C:\RFSimWorker\worker.toml` kurulu CST sürümünü, Python API dizinini ve boş alanı okur.
2. **İşçiyi başlat** ile çalışan mock iş hosta bağlanır, küçük sonuç paketini yükler ve hosttaki SHA-256 doğrulamasını sınar. **Bağlantıyı test et** yalnız tek seferlik kayıt gönderir ve işi başlatmaz. Host ekranında istemci, işçi döngüsü çalışırken çevrimiçi görünmelidir.
3. Yalnız bundan sonra `gpu=false`, global 32 ağ ve en fazla 20 dakika sınırıyla tek CST 2025 işi çalıştırılır. CST 2026'da üretilmiş bir proje dosyası CST 2025'e geri açılmaya çalışılmaz; model CST 2025 içinde sabit Python kaynakları ve üretilen VBA ile yeniden kurulur.

Mock işçisi güvenli biçimde durdurulduktan ve depo güncellendikten sonra gerçek pilotu etkinleştiren komut şudur:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\enable-school-cst2025-pilot.ps1
```

Betik mevcut host URL'sini ve işçi kimliğini korur, eski yapılandırmayı zaman damgalı olarak yedekler, CST 2025 yürütülebilir dosyası ile Python API dizinini doğrular ve yalnız `widefield-compact-M01-near-control-global32` vaka kimliğine izin verir. Uzak iş geometri, malzeme, ağ, GPU veya solver ayarlarını değiştiremez. Betik bittikten sonra GUI yeniden açılır ve **Bağlantıyı test et** çıktısında `CST 2025` ile `cst-widefield-cst2025-pilot-v3` görülmeden gerçek iş gönderilmez.

İlk gerçek deneme geometri bloklarının 120'sini tamamladı; son solver ayarı bloğunda CST 2025'in `.FrequencySampleRuleLin` yöntemini desteklememesi nedeniyle solver başlamadan kapandı. Proje kapanışı doğrulandı. `v3` kaynağı CST 2025'te yalnız bu desteklenmeyen çağrıyı atlar; `.FrequencySamples "4001"`, bant, ağ ve fiziksel model korunur.

Mevcut donmuş kaynaklar 2026 API yolunu ve sürüm kaydını sabit içeriyorsa doğrudan çalıştırılmaz. `scripts/prepare-portable-cst-source.py` bunları yeni ve değişmez bir kaynak sürümüne türetir; yerel API yolunu `CST_PYTHON_LIBRARIES`, kaydedilen sürümü `CST_EXPECTED_VERSION` üzerinden alır ve bütün kaynak karmalarını yeniden üretir. `scripts/prepare-cst-runner.ps1` bağdaştırıcı ile yeni manifest karmalarını verir. Bu işlem kaynak uyumluluğunu kanıtlamaz; yanlış 2026 kaydıyla 2025 sonucu yayımlanmasını önler ve gerçek pilotun izlenebilir olmasını sağlar.

İlk CST pilotunda `cleanup_after_upload=false` ve bağdaştırıcıda `--compact` kapalı kalır. Başlangıç ve bitiş boş alanı, CST içi sürüm, model/VBA karmaları, solver dönüşü, karmaşık S-parametreleri ve kapanma kanıtı incelenir.

Pilot geçerse iki aşamalı temizlik açılır:

- Bağdaştırıcının `--compact` seçeneği, doğrulama tamamlandıktan sonra büyük `.cst` çalışma ağacını ve ham mesh dosyalarını kaldırır. `model.vba`, donmuş Python kaynakları, kaynak manifesti, parametreler, karmaşık S-parametreleri, mesh özeti, günlükler, kalite kaydı ve silinen büyük dosyaların karma/boyut bilgileri pakette kalır.
- `cleanup_after_upload=true`, host paketi kabul edip paket karmasını aynen geri bildirdikten sonra istemcideki iş klasörünü ve ZIP'i kaldırır. İstemcide yalnız küçük bir aktarım makbuzu kalır. Yükleme belirsizse veya karma eşleşmezse hiçbir şey silinmez.

29,5 GB boş alan tek küçük pilot için denenebilir; uzun kuyruk için yeterli olduğu kabul edilmez. İstemci, `min_free_gb=15` altına düştüğünde yeni iş kiralamaz. İlk pilotun tepe disk tüketimi ölçüldükten sonra bu eşik ve aynı anda izin verilen iş boyutu yeniden belirlenir.

GPU bir zorunluluk değildir. İstemci GPU gerektirmediğini bildirir ve ilk işler `gpu=false` kullanır. Bu durum sonucu değiştirmemeli, süreyi uzatabilir; sürüm ve solver davranışı gerçek pilot kaydından doğrulanır.

Kurulum taraması hem `Program Files` hem de `Program Files (x86)` altındaki standart CST 2024–2027 yollarını denetler. Sonuç boşsa gerçek CST işi gönderilmez; önce okul bilgisayarındaki `CST DESIGN ENVIRONMENT.exe` ve `AMD64\python_cst_libraries` yolları bulunup `worker.toml` içindeki `cst_roots` değeri düzeltilir.
