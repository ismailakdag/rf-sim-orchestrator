# RF Sim Orchestrator

Bu depo, birbirinden bağımsız RF/EM simülasyon işlerini bir ana bilgisayar ile dışarı doğru bağlantı kuran Windows işçileri arasında dağıtmak için küçük ve denetlenebilir bir altyapıdır. Ana bilgisayar değişmez işleri SQLite kuyruğunda saklar. Her işçi aynı anda yalnız bir iş kiralar, yerel izin listesindeki bir çalıştırıcıyı çağırır ve tam sonuç paketini geri yükler.

Bu yazılım tek bir CST çözümünü hızlandırmaz. Birden fazla bağımsız koşuyu farklı bilgisayarlarda yürütmeye yarar. Okul bilgisayarı istemcisi CST'yi iç ağdan erişilebilir yapmaz; hosta yalnız dışarı doğru HTTPS bağlantısı kurar. Canlı CST kullanımı, kurulum ve lisans doğrulamasından sonra tek küçük pilotla açılır.

Mimariyi, hata durumlarını ve okul kurulumu öncesi karar kapılarını tarayıcıda görmek için [çevrimdışı sunumu](presentation/index.html) açabilirsiniz.

CST 2025/2026 seçimi, boş işçiye uzaktan başlatma, kullanıcı CST projesi yükleme ve çoklu bilgisayar desteğinin planlanan kapsamı [uzak CST çalışma sistemi yol haritasında](docs/remote-cst-roadmap-tr.md) tutulur.

## Güvenlik ve hata davranışı

Tüm HTTP uçları en az 32 karakterlik aynı Bearer belirteciyle doğrulanır. Düz HTTP, belirteci ağ üzerinde korumaz; gerçek ağda HTTPS kullanılmalıdır. TLS doğrudan host yapılandırmasında veya kurumun HTTPS ters vekilinde sonlandırılabilir. Belirteci Git'e ya da TOML dosyasına yazmayın; `RF_SIM_TOKEN` ortam değişkeninde tutun.

Uzak iş belgesi bir komut, Python yolu veya betik yolu taşıyamaz. Ana bilgisayar yalnız izin verilen çalıştırıcı adlarını kabul eder. İşçi bu adı kendi TOML dosyasındaki sabit betik, sabit Python ve sınırlı parametre şemasıyla eşler. Alt süreç `shell=False` ile başlatılır. Bu sınır, normal iş payload'ının keyfî bir kabuk komutuna dönüşmesini önler. Ana bilgisayarın veya işçinin tamamen ele geçirilmesine karşı güvenlik garantisi değildir; bu makineler ve yerel yapılandırmaları güven sınırındadır.

Bir lease, yani süreli iş sahipliği kaydı, heartbeat denen düzenli yaşam sinyali gelmezse sona erer. Pahalı bir solver'ın uzak bilgisayarda hâlâ çalışıp çalışmadığı bilinemez. Bu nedenle iş `needs_attention` durumuna geçer ve otomatik olarak yeniden çalıştırılmaz. Aynı işçi kimliği, bu belirsizlik çözülene kadar yeni iş alamaz; başka işçiler bağımsız işleri sürdürebilir. Sabit dış çalıştırıcı zaman aşımında yalnız üst Python sürecini öldürmenin CST alt sürecini durdurduğu kanıtlanamayacağı için süreç otomatik sonlandırılmaz; süreç kimliği (PID) ve yerel durum kaydedilir. Operatör eski işçiyi ve yerel arşivi inceledikten sonra işi açık gerekçeyle yeniden kuyruğa alabilir veya terminal `resolved` durumunda kapatabilir. Dağıtık sistemde tam “yalnız bir kez” yürütme garantisi verilemez; uygulama belirsizliği görünür ve kalıcı tutar.

Sonuçlar ZIP64 ile 1 MiB parçalar halinde aktarılır. Ana bilgisayar sıkıştırılmış ve açılmış boyut sınırlarını, mutlak veya üst dizine çıkan yolları, ters eğik çizgileri, sembolik bağlantıları, yinelenen üyeleri ve manifest kapsamını denetler. `manifest.json`, kendisi dışındaki her dosyanın boyutunu ve SHA-256 karmasını içerir. İndirme komutu hem paket karmasını hem de iç dosyaları yeniden doğrular.

Tam paket şu alanların her birinde en az bir dosya ister:

| Dizin | Beklenen kanıt |
|---|---|
| `model/` | Kaydedilmiş model veya mock model |
| `source/` | Üretici kaynakları ve üretilmiş VBA |
| `parameters/` | Birimleri ve kaynağı belirli parametreler |
| `results/` | Frekans ekseni ile karmaşık S-parametreleri |
| `mesh/` | Gerçek ağ bilgisi veya açıkça mock kaydı |
| `logs/` | Çalıştırıcı ve solver yaşam döngüsü |
| `quality/` | Bütünlük, pasiflik, enerji ve yakınsama kontrolleri |

## Yerel mock gösterimi

Windows PowerShell'de Python 3.11 veya daha yenisiyle:

```powershell
cd E:\rf-sim-orchestrator
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
$env:RF_SIM_TOKEN = py -c "import secrets; print(secrets.token_hex(32))"
```

Birinci terminalde ana bilgisayarı başlatın:

```powershell
.\.venv\Scripts\rf-sim.exe host --config examples\host.toml
```

İkinci terminalde işi gönderip tek işçi çevrimini çalıştırın:

```powershell
.\.venv\Scripts\rf-sim.exe submit --url http://127.0.0.1:8765 examples\job.json
.\.venv\Scripts\rf-sim.exe worker --config examples\worker.toml --once
.\.venv\Scripts\rf-sim.exe status --url http://127.0.0.1:8765 demo-0001
.\.venv\Scripts\rf-sim.exe results --url http://127.0.0.1:8765 demo-0001 --output demo-data\downloaded\demo-0001.zip
```

Mock çalıştırıcı CST'ye bağlanmaz. Yine de model, VBA, parametre, uzun biçimli karmaşık S-parametresi, ağ, günlük ve ayrı kalite kayıtlarını üretir; böylece taşıma ve doğrulama hattı uçtan uca sınanır.

## Ana bilgisayar ve okul işçisi kurulumu

Ana bilgisayarda `examples/host.toml` dosyasını çalışma kopyasına alın; `data_dir`, dinleme adresi, port, izinli çalıştırıcılar ve boyut sınırlarını belirleyin. Gerçek ağda TLS veya güvenilen bir HTTPS ters vekili olmadan `0.0.0.0` üzerinde servis açmayın. Güvenlik duvarında yalnız gerekli ağlardan gelen bağlantıya izin verin.

Okul bilgisayarında depoyu ve sanal ortamı ayrı bir klasöre kurun. `examples/worker.toml` kopyasında ana bilgisayarın erişilebilir HTTPS adresini, benzersiz işçi kimliğini ve yerel veri dizinini yazın. Aynı güçlü belirteci kullanıcı kapsamındaki `RF_SIM_TOKEN` ortam değişkenine koyun. Ardından `python -m rfsim worker --config ... --once` veya kurulu `rf-sim worker` komutuyla önce mock işi doğrulayın. Sürekli çalışma daha sonra Windows Görev Zamanlayıcı'da kullanıcı oturumu ve kurum politikalarıyla uyumlu bir görev olarak kurulabilir.

Okul bilgisayarı için kurulum ve GUI:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install-school-worker.ps1 -HostUrl https://HOST-ADRESI -WorkerId OKUL-PC-01
$env:RF_SIM_TOKEN = "HOST-ILE-AYNI-UZUN-BELIRTEC"
C:\RFSimWorker\RF-Sim-Okul-Istemcisi.cmd
```

GUI'deki **Bağlantıyı test et** düğmesi yalnız tek seferlik erişim ve yetenek kaydı gönderir; kuyruktan iş almaz ve sürekli çevrimiçi kalmaz. Kuyruktaki işi almak için **İşçiyi başlat** düğmesine basılır. İstemci çalışan işi kesmeden güvenli durdurma ister. Host bilgisayarında `rf-sim monitor-gui --url https://HOST-ADRESI` ile istemcilerin çevrimiçi durumu, boş diski, CST sürümü ve etkin işi izlenebilir. Aynı bilgi `rf-sim workers --url ...` ile JSON olarak alınır. `rf-sim probe --config ...` hosta bağlanmadan CST kurulumunu ve disk kapısını denetler.

Doğrulanmış okul pilotunda `scripts/enable-school-cst2025-pilot.ps1`, DPAPI ile kullanıcıya bağlı saklanan belirteç üzerinden işçiyi gizli arka planda hemen başlatır ve Windows oturum açılışına ekler. Bu pilotta her iş için GUI düğmesine basılmaz. Genel kurucu ve kontrollü beklenmeyen-kapanma yeniden başlatması yol haritasında açık iştir.

İşçi yalnız dışarı doğru HTTP(S) isteği gönderdiği için okul bilgisayarında gelen bağlantı açılması gerekmez. Ancak ana bilgisayar URL'sinin okul ağından erişilebilir olması gerekir; Python betikleri NAT'ı kendiliğinden aşmaz. Bu sürümün büyük sonuç yüklemesi doğrudan HTTP(S) bağlantısı kurar ve kurumsal proxy üzerinden çalışmayı desteklemez. Proxy gerekiyorsa yükleme istemcisi ayrıca geliştirilip sınanmalıdır. Bu depo proxy kurmaz veya gerçek okul bağlantısını denemez.

## İş belgesi ve durumlar

`examples/job.json` tam bir örnektir. `job_id` değişmez kimliktir; aynı belge tekrar gönderildiğinde işlem idempotenttir. Aynı kimlikle farklı içerik reddedilir. `source.sha256`, çalıştırılacak donmuş kaynak sürümünü bağlar. `deadline_utc` saat dilimli ve gelecekte olmalıdır. Parametrelerin birimleri anahtar adında veya yapılandırılmış değerde açık olmalıdır.

Durumlar `queued`, `leased`, `completed`, `failed`, `expired`, `needs_attention` ve `resolved` değerlerini alır. `completed`, paketin host'a ulaştığını ve dosya bütünlüğü kontrollerinden geçtiğini gösterir; bilimsel geçerlilik, mesh yakınsaması veya fiziksel doğrulama anlamına gelmez. Bunlar `quality/` kayıtlarında ayrı sonuçlardır. Worker hatası `failed` olur; bağlantı, makine veya dış solver durumu belirsizse iş `needs_attention` olur. Yeniden kuyruğa alma açık bir operatör kararıdır:

```powershell
rf-sim requeue --url https://HOST:8765 JOB-ID --reason "Okul bilgisayarında CST ve yerel arşiv kontrol edildi; solver çalışmıyor."
```

İşin değişmez son tarihi geçtiyse aynı iş yeniden kuyruğa alınamaz. Operatör, ilgili bilgisayarda etkin solver kalmadığını doğruladıktan sonra belirsiz kaydı silmeden terminal durumda kapatabilir. Bu işlem sonuçları veya yerel kanıt dosyalarını silmez ve otomatik tekrar başlatmaz; işçiyi yeni bir `job_id` alabilmesi için serbest bırakır:

```powershell
rf-sim resolve --url https://HOST:8765 JOB-ID --reason "Okul bilgisayarı kontrol edildi; bu işe ait etkin solver veya alt süreç bulunmuyor."
```

## CST 2025/2026 bağdaştırıcısı

`adapters/candidate_local_metal_v2.py`, mevcut `candidate-local-metal-v2/night_case.py` akışına isteğe bağlı bir köprüdür. Örnek yapılandırma `examples/cst-candidate-local-metal-v2.toml.example` içindedir. Kaynak kanıtı olarak depoya kopya alınmamış, yalnız mevcut donmuş kaynak manifestinin SHA-256 değeri ve yerel yol kullanılmıştır.

Bağdaştırıcı CST sürümüne sabit bir kurulum yolu kullanmaz. İşçi yapılandırmasındaki `cst_python_libraries`, seçilen yerel kurulumun resmi Python kitaplıklarını alt sürece verir. CST 2025 ile CST 2026 arasında API veya proje biçimi farkı bulunabileceği için eski `.cst` dosyası geri açılmaz; donmuş Python kaynakları ve üretilen VBA ile model hedef sürümde yeniden kurulur. Uyumluluk yine tek küçük gerçek pilotla kanıtlanmalıdır. Yerel `script_sha256` değeri kurulum sırasında hesaplanmalıdır. Çalıştırıcı kaynak manifestini ve içindeki her kaynak karmasını yeniden doğrular, legacy iş belgesini yerel olarak üretir, `night_case.py` betiğini sabit komutla çağırır ve doğrulanmış arşivi ortak paket düzenine taşır.

Okul bilgisayarındaki doğrulanmış CST 2025 yolu için mock işçi güvenli biçimde durdurulduktan sonra `scripts/enable-school-cst2025-pilot.ps1` çalıştırılır. Bu betik mevcut bağlantı ayarlarını korur, CST bağımlılıklarını kurar ve uzaktan yalnız manifestteki tek vaka kimliğinin seçilebildiği sabit global32 CPU pilotunu ekler. Varsayılan yürütülebilir dosya `C:\Program Files (x86)\CST Studio Suite 2025\CST DESIGN ENVIRONMENT.exe` yoludur.

Sınırlı disk kullanımı için iki ayrı, varsayılan olarak kapalı seçenek vardır. Bağdaştırıcıdaki `--compact`, doğrulanmış model VBA'sını ve yeniden üretim kaynaklarını tutup büyük ham CST çalışma ağacını paketlemeden önce kaldırır. İşçideki `cleanup_after_upload=true`, host paketi doğrulayıp aynı SHA-256 değerini döndürdükten sonra yerel iş klasörünü ve yükleme ZIP'ini siler; yalnız makbuz kalır. İlk mock ve CST pilotlarında ikisi de kapalı tutulmalıdır. Ayrıntılı sıra [okul bilgisayarı pilot belgesindedir](docs/school-pc-pilot-tr.md).

Bağdaştırıcı örneğindeki parametre sınırları kampanya öncesi mevcut `night_case.py` sözleşmesine göre genişletilmelidir. Eksik veya fazla parametre kapalı güvenli biçimde reddedilir. Yeni bir kaynak sürümü ya da betik değişikliği yeni karmalar ve inceleme gerektirir.

## Wake-on-LAN

```powershell
rf-sim wol --mac 00-11-22-33-44-55 --broadcast 192.168.1.255 --port 9
```

Komut MAC adresini, IPv4 broadcast adresini ve portu doğrulayıp tek bir magic packet yollar. Uzaktan açılma garantisi vermez. BIOS/UEFI ve ağ kartında Wake-on-LAN etkin olmalı; bilgisayarın güç ve ağ durumu desteklemeli; paket aynı yayın alanından ya da önceden yapılandırılmış, erişilebilir bir relay üzerinden gönderilmelidir.

## Geliştirme ve test

Yerel CST kuyruğu ve tüm uzak bilgisayarları aynı pencerede izlemek için
[bilgisayar izleme panelini](docs/host-monitor-tr.md) kullanın. Panel yeni
işçileri otomatik listeler; yenilemek için ajan/model çağrısı gerekmez.

```powershell
py -3.11 -m unittest discover -s tests -v
```

Test paketi doğrulama, yol geçişi reddi, lease süresi sonunda otomatik tekrar yapılmaması ve gerçek host/worker alt süreçleriyle mock uçtan uca aktarımı kapsar.
