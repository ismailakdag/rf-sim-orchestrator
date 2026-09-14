# Uzak CST çalışma sistemi yol haritası

Güncelleme: 14 Eylül 2026, 21:21 Türkiye saati

Bu belge, mevcut pilotun ardından geliştirilecek ürün kapsamını kaydeder. Aşağıdaki maddeler tamamlanmış özellikler değildir; uygulama ve kabul testleri bitmeden üretim özelliği sayılmaz.

İlk sağlamlaştırma adımı `0.2.4` sürümünde uygulanmıştır: iç CST solver bütçesi ile dış gözetmen sınırı arasında kapanış payı bulunur; işçi görünür Abort ve License pencerelerini otomatik tıklamadan heartbeat durumuna taşır. Genel proje yükleme, sürüm seçimi ve çoklu bilgisayar arayüzü hâlâ aşağıdaki yol haritasındadır.

## Hedef kullanım

Kullanıcı, host arayüzünden bir CST işi seçebilmeli veya kendi CST proje paketini yükleyebilmeli. Host yalnız uygun ve boş bir işçiye değişmez işi vermeli. İşçi doğru CST sürümünü seçmeli, aynı anda tek solver çalıştırmalı, yaşam döngüsünü kaydetmeli, doğrulanmış sonucu hosta yüklemeli ve kabul makbuzundan sonra geçici çalışma verisini temizlemelidir.

Birden fazla bilgisayar eklendiğinde her kontrol–kontrast çifti aynı bilgisayar ve CST sürümünde kalır. Farklı sürümlerin mutlak S-parametreleri tek veri havuzunda eş örnek gibi kullanılmaz.

## Öncelik 0: güvenilir süreç yaşam döngüsü

1. İşçi `idle`, `preparing`, `solver_running`, `exporting`, `uploading`, `needs_attention` ve `stopping` aşamalarını ayrı raporlar.
2. Başlatılan Python, CST GUI ve solver süreçleri PID yanında oluşma zamanı, yürütülebilir dosya yolu ve iş kimliğiyle kaydedilir. Böylece daha önce açık CST süreçleri ile işe ait süreçler ayrılır.
3. Zaman aşımında önce CST sonuç ve ileti günlükleri okunur. İşe ait süreç ağacı kesin olarak tanımlanabiliyorsa yapılandırılmış güvenli kapatma uygulanabilir; belirsizlikte otomatik öldürme ve otomatik yeniden deneme yapılmaz.
4. `needs_attention` durumundaki işçi yeni iş kiralayamaz. Operatör, kalan solver bulunmadığını doğruladıktan sonra işi gerekçeli olarak kapatır veya yeni kimlikle yeniden dener.
5. İşçi Windows oturum açılışında başlar. Beklenmeyen kapanma için sınırlı yeniden başlatma ve görünür hata günlüğü eklenir; kullanıcı tarafından istenen güvenli durdurma bu yeniden başlatmayı bastırır.
6. Host, kuyruk boşluğu ile işçi boşluğunu ayrı gösterir. Gönderim anında seçilen işçinin çevrimiçi, boş, yeterli diskli ve istenen CST sürümüne sahip olması zorunludur.

## Öncelik 1: CST 2025 ve CST 2026 uyumluluğu

1. Her çalıştırıcı desteklediği CST ana sürümlerini, Python API yolunu, GPU gereksinimini ve proje üretim biçimini yetenek manifestinde ilan eder.
2. İş belgesi `required_cst_major` ve gerekirse belirli `required_worker_id` taşır. Host yalnız tam eşleşen işçiye lease verir.
3. Aynı fiziksel model Python/VBA kaynağından hedef CST sürümünde yeniden kurulabilir. CST 2026 ile kaydedilmiş proje CST 2025'te sessizce açılmaya çalışılmaz.
4. 2025 ve 2026 için küçük bir uyumluluk matrisi tutulur: model kurma, ağ, solver, 4.001 kompleks örnek dışa aktarımı, enerji, pasiflik, proje kapanışı ve kaynak karmaları.
5. Sürüm veya çalıştırıcı değişikliği önce tek kontrol–kontrast köprüsünü geçer; büyük kuyruk bundan sonra açılır.

## Öncelik 1: kullanıcı CST projesi yükleme

1. Host, yetkili kullanıcının yüklediği `.cst` dosyasını veya yeniden üretim paketini SHA-256, boyut, hedef CST sürümü ve açıklama ile değişmez artefakt olarak saklar.
2. Yükleme, doğrudan komut veya betik gönderemez. Ayrı `cst-project-v1` çalıştırıcısı yalnız hostta kayıtlı artefakt karmasını açar ve yerel izin verilen solver işlemlerini uygular.
3. İş gönderilmeden önce port sayısı, frekans bandı, solver türü, beklenen sonuçlar, süre sınırı, gereken disk alanı ve temizleme politikası kullanıcıya gösterilir.
4. Projede gömülü makro veya sürüm yükseltme davranışı ayrıca sınanır. Güvenli olduğu doğrulanmayan proje otomatik çalıştırılmaz.
5. Kaynak proje hostta korunur. İşçideki kopya yalnız sonuç paketi doğrulanıp host makbuzu alındıktan sonra silinir.
6. Kuyruk doluysa iş bekler; çalışan solver varken yeni CST örneği başlatılmaz.

## Öncelik 2: izleme ve çoklu bilgisayar

- Web arayüzü işçi çevrimiçi durumunu, CST sürümünü, boş diski, etkin işi, geçen süreyi ve son heartbeat'i gösterir.
- Yükleme, iş oluşturma, durdurma isteği, gerekçeli yeniden deneme ve sonuç indirme işlemleri denetim günlüğüne yazılır.
- Üçüncü ve dördüncü bilgisayar aynı yetenek sözleşmesiyle eklenir. Zamanlama yalnız bağımsız işleri paralelleştirir; tek CST çözümünü bilgisayarlar arasında bölmez.
- Geçici Cloudflare adresi yerine kalıcı HTTPS alan adı, erişim kontrolü, belirteç yenileme ve yedekleme planı hazırlanır.

## Kabul ölçütleri

- Aynı iş kimliği iki solverda eşzamanlı çalışmaz.
- Boş olmayan veya `needs_attention` durumundaki işçiye yeni solver işi verilmez.
- Yanlış CST sürümü, kaynak karması, disk sınırı veya izin verilmeyen parametre solver başlamadan reddedilir.
- Bağlantı kesilmesi, solver zaman aşımı, uygulama çökmesi ve yarım yükleme senaryoları uçtan uca test edilir.
- Sonuç paketi hostta ve yeniden indirmede aynı SHA-256 değerini verir; bilimsel kalite denetimleri taşıma başarısından ayrı tutulur.
- Temizlik yalnız host kabulünden sonra yapılır ve yeniden üretim kaynağı hiçbir zaman tek kopya olarak işçide bırakılmaz.
