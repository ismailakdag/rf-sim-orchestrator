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
