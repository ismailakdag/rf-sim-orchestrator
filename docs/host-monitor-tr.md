# RF Sim bilgisayar izleme paneli

`rf-sim monitor-gui --url http://127.0.0.1:8876 --local-current <CURRENT.json>`
komutu tüm kayıtlı işçileri ve isteğe bağlı yerel Python kuyruğunu tek pencerede
gösterir. Token `RF_SIM_TOKEN` ortam değişkeninden okunur. Windows başlatıcısı
`scripts/open-host-monitor.ps1`, `-TokenFile` ve `-LocalCurrent` seçeneklerini alır.

Panel 10 saniyede bir salt okunur veri alır. Bilgisayar adı, bağlantı, işlem,
son bildirilen aşama, bildirilen solver süresi, tamamlanan işler, boş disk ve CST
sürümü görünür. Makine seçilince iş kimliği, son sinyal ve hata açıklaması açılır.
Uzak tamamlanma sayıları host geçmişini, yerel sayılar etkin kampanyayı kapsar.
Disk değerleri son işçi bildirimindendir. Tamamlanma yüzdesi veya tahmini bitiş
zamanı bilinmediğinde uydurulmaz.

Uzak işçinin son 90 saniyedeki bildirimi veya ona ait canlı iş heartbeat'i
çevrimiçi kanıtıdır. Haber kesildiğinde önceki çalışma durumu güncel kabul edilmez.
Hosta erişilemediğinde eski satırlar bilinmiyor olarak tutulur; hata pencereleri
tekrar tekrar açılmaz. Yeni bilgisayarlar aynı hosta kaydolunca otomatik görünür.
Bu, PC'nin elektrik durumunu ölçmez; işçinin hostla haberleşmesini gösterir.

Yerel satır `CURRENT.json` üzerinden etkin kampanyayı bulur ve işin model yolunu
HF solver komutuyla eşleştirir. Sadece eski `running` dosyasına güvenmez.
Panel ağ okumasını arka plan iş parçacığında yapar; Tk arayüzü ana iş
parçacığında güncellenir. Aynı anda bir yenileme yapılır. Duraklatmak veya pencereyi
kapatmak simülasyonları etkilemez. Ajan/model çağrısı kullanılmaz.

## S-parametreleri

İzleme panelindeki **S-parametreleri** düğmesi tamamlanan koşuları açar.
Kampanya, iş kimliği veya PC ile ara; Ctrl ile en fazla dört koşu seç ve
**Seçilenleri çiz** düğmesine bas. Kanal S11/S12/S21/S22; görünüm dB, doğrusal
genlik, açılmış faz, gerçek veya sanal bileşen olabilir. Fare en yakın gerçek
frekans örneğinin değerini gösterir. Araç çubuğu yakınlaştırma, kaydırma ve
grafik kaydetme sağlar. İlk seçili koşunun tüm kompleks verisi CSV, modeli VBA
olarak kaydedilebilir; modeller açılmaz veya çalıştırılmaz.

Varsayılan analiz aralığı 1–4,5 GHz'dir. **Tek bir rezonansı çevreleyen aralığı**
seçerek Uygula'ya bas. f0, aralıktaki en derin çukur veya en yüksek tepenin
örnek frekansıdır; güven aralığı veya alt örnek hassasiyeti iddiası yoktur.
BW kesişimleri güçte doğrusal interpolasyonla hesaplanır:

- Tepe: maksimum gücün yarısındaki iki kesişim (yaklaşık −3,01 dB).
- Çukur: uçlardaki güç medyanlarının ortalaması ile minimum gücün orta seviyesi.
  Uçlar 1 dB'den fazla farklıysa veya derinlik 3 dB'den azsa değer üretilmez.

`Q_BW = f0/BW` yalnız seçilmiş yöntemin eğri genişliği göstergesidir.
Özellikle yansıma çukurunda bu değer fiziksel yüklü/yüksüz rezonatör Q'su
değildir. Fiziksel Q için uygun rezonatör modeli, kuplaj ve kompleks eğri uyumu
gerekir. [scikit-rf Q açıklaması](https://scikit-rf.readthedocs.io/en/latest/tutorials/Q-Factor.html)
bu ayrımı açıklar. Çoklu/asimetrik rezonanslar ve değişen taban ayrıca yorumlanmalıdır.
Eşiklerden biri aralık dışında kalırsa veya ekstremum sınırdaysa BW/Q boş kalır;
neden grafiğin altında yazılır. Farklı simülasyonların üst üste çizilmesi onların
aynı geometri, mesh veya deney grubunda olduğunu kanıtlamaz.

Yerel dosyalar doğrulama manifestindeki hash ile kontrol edilir. Uzak ZIP yalnız
seçilince indirilir; host hash'i ve paket manifesti doğrulanır. Önbellek
`%LOCALAPPDATA%/RFSim/plot-cache` içindedir. Grafikler çözümü yeniden başlatmaz.
Matplotlib yalnız izleme bilgisayarına gerekir: `pip install -e ".[monitor]"`.
Mevcut koşul arşivlerinde sensör önizlemesi bulunmadığından bu sürüm görüntü üretmez.
