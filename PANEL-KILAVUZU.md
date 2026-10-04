# TradeIQ v0.3 — panel kullanım kılavuzu

TradeIQ geçici ürün adıdır. Bu teslim **yerel, tek kullanıcılı ürün prototipidir**. Hazır demo, gerçek fiyatla sanal işlem ve Binance Spot Testnet doğrulama bağlantısı içerir. Gerçek hesapta emir gönderme ve para çekme yolu yoktur.

## Paneli açın

1. Paket klasöründeki `Paneli-Ac.cmd` dosyasına çift tıklayın. Açılan pencere uygulama boyunca açık kalmalıdır.
2. Tarayıcıda `http://127.0.0.1:8765` adresini açın.
3. Bitirince komut penceresinde Ctrl+C ile uygulamayı durdurun. Program kapalıyken fiyat ve stop kontrolü çalışmaz.

Python 3.10+ gerekir. Bu bilgisayarın mevcut Codex Python'u otomatik bulunur; yoksa normal Python kullanılır. Ek paket gerekmez. Alternatif: `python dashboard.py` veya PowerShell'de `./Paneli-Ac.ps1`.

8765 portu doluysa `python dashboard.py --port 8767` ile başlayın ve aynı portu tarayıcıda açın. Mevcut çalışma ortamından bağımsız yeni bir deneme için `python dashboard.py --runtime runtime-yeni --port 8767` kullanın. Eski kayıtlar silinmez.

## Önce hazır demoyu deneyin

“Kurgusal örnek” kaynağını seçin. “Sonraki adımı oynat” her basışta bir olay işler. İlk olay reddedilen alım, ikinci olay başarılı sanal alımdır. Bakiye alımda azalırken portföyün çoğunun korunmasını görebilirsiniz. Sonraki adımlar satış reddi, satış sinyali, günlük kayıp sınırı, stop ve ertesi gün kâr-al çıkışını gösterir. 12 adım tamamlandığında başlangıç 1000 USDT, sonuç yaklaşık 987,82 USDT'dir. Bu kurgusal örnek yatırım performansı değildir.

## Gerçek Binance fiyatlarıyla sanal işlem

1. Veri kaynağını “Binance fiyatları · sanal işlem” yapın.
2. SOL/USDT, BTC/USDT veya ETH/USDT seçip “Seçimi uygula”ya basın.
3. “Fiyatı yenile” ile veri bağlantısını kontrol edin. Bunun için API anahtarı gerekmez.
4. “Sanal alım” ve “Sanal satış” ile muhasebeyi deneyin. Her düğme yeni fiyat alır. İşlemler yalnızca yerel simülasyon defterindedir; borsaya emir gönderilmez.
5. “Başlat” fiyatları yaklaşık 15 saniyede bir takip eder. Otomatik seçenek kapalıysa yeni alım sinyali uygulanmaz; stop ve kâr-al kontrolleri yapılır.
6. Otomatik EMA seçeneği açıkken “Başlat”, kapanmış bir dakikalık mumlarda EMA 9/21 kesişimini izler. Yukarı kesişimde alım, aşağı kesişimde çıkış sinyali oluşur. Aynı mum sinyali tekrar işlenmez. Bu gösteri stratejisi Supertrend değildir ve performansı doğrulanmamıştır.

“Duraklat” yeni otomatik girişleri durdurur. Açık pozisyon varsa uygulama açık olduğu sürece stop/kâr-al kontrolü devam eder. Bağlantı koparsa kontroller de yeni fiyat gelene kadar yapılamaz; panel son bilinen portföy değerini gösterir. Fiyat yaşını ve hata mesajını kontrol edin. “Duraklat”, bütün pozisyonları kapatan düğme değildir; kapatmak için sanal satış yapın.

Kaynak/parite, açık pozisyon varken veya takip açıkken değiştirilemez. Her gerçek fiyat paritesinin ayrı muhasebe defteri vardır; bunlar birleşik çok-pariteli bir hesap değildir. Hazır demo kendi içinde birden fazla kurgusal parite içerir. Bütün bakiyeler USDT cinsinden sanaldır.

## Binance test ortamı

Binance'in [Spot Testnet](https://testnet.binance.vision/) sayfasından oluşturduğunuz **test ortamına ait HMAC API anahtarı ve gizli anahtarı** paneldeki alanlara kendiniz girin. Gerçek hesap anahtarlarını kullanmayın. Anahtarları sohbete veya dosyaya yazmanız gerekmez.

“Bağlantıyı kontrol et”, yalnızca test hesabını okur; seçili test bakiyelerini gösterir. Anahtarlar uygulamanın belleğinde tutulur, dosyaya ve olay geçmişine yazılmaz. Alanlar gönderim sonrasında boşaltılır. “Bağlantıyı kaldır” veya programı kapatmak bağlantıyı sona erdirir. Bu prototip belleği sıfırlama ya da işletim sistemi düzeyinde sır koruması garantisi vermez.

“Test emrini doğrula”, seçili parite için 5–100 USDT aralığında MARKET BUY isteğini **testnet `/api/v3/order/test`** uç noktasına gönderir. Binance bu isteği doğrular; eşleştirme motoruna iletmez. Dolayısıyla bu düğme test hesabında bile gerçekleşen alım oluşturmaz ve sanal yerel defteri değiştirmez. HMAC imzası, zaman bilgisi, parite ve emir biçimi bu yolla sınanabilir. API anahtar türü RSA/Ed25519 ise bu sürüm desteklemez.

Testnet hesabı gerçek kullanıcı anahtarları olmadan uçtan uca doğrulanamadı. İmza ve uç nokta ayrımı otomatik testlerde sahte yanıtlarla doğrulandı. Test hesabında gerçekleşen emir, kısmi gerçekleşme ve hesap uzlaştırması sonraki aşamadır.

## Risk ve kayıtlar

Sanal başlangıç 1000 USDT; işlem başına nominal tavan 250 USDT; risk hedefi %1; sabit stop %5; kâr-al %10; günlük yeni alım sınırı %2. Ücret her yönde %0,1; kayma %0,05. Bunlar simülasyon varsayımlarıdır. Fiyat boşluğunda stop fiyatından çıkış garantisi yoktur; %2 günlük sınır kaybın %2'de kalacağı anlamına gelmez.

Panel başlangıçta `runtime` altında kayıt oluşturur. Demo, parite muhasebesi ve seçili kaynak korunur. Yeniden başlatmada otomatik giriş varsayılan olarak kapalıdır; açık gerçek-fiyat sanal pozisyonları için fiyat/stop kontrolü arka planda devam eder. Testnet anahtarları geri yüklenmez. Uygulama kaydı bozuksa bakiyeyi sessizce sıfırlamak yerine açılışı durdurur.

CSV raporu, seçili yerel defterin olaylarını indirir. Her olay gerçekleşen emir değildir; bekleme, emir reddi ve sınır nedeniyle engellenen alımları da içerir. İşlem fiyatı/miktarı/ücret ayrıntıları ilgili runtime checkpoint'indeki `fills` bölümündedir. Panelde son 20 olay, grafikte son 200 olay gösterilir. Kayıt dosyasını aynı anda iki uygulama süreciyle kullanmayın.

## Doğrulama ve ürün sınırları

61 otomatik test geçti. Önceki 21 muhasebe testine ek olarak bağlantı adresi kısıtları, HMAC imzası, yönlendirme reddi, istek sınırı beklemesi, eski fiyat/mum, bilgisayar saati farkı, tek mum sinyali, duraklatmada stop, kayıtla yeniden başlatma, anahtarların kaydedilmemesi ve yerel panel istek doğrulaması test edildi.

4 Ekim 2026'da herkese açık Binance fiyatı ve kapanmış mum verisi gerçek bağlantıyla alındı. Kontrol sonucu `results/public-connection-check.json` içinde; bu bir fiyat tahmini veya işlem sonucu değildir. Otomatik test çıktısı `results/review-test-results.txt` içindedir. Masaüstü ve 390 piksel mobil görünüm tarayıcıda kontrol edildi.

Bu panel internette yayınlanmış SaaS değildir; kullanıcı hesabı, abonelik, ödeme, çoklu kullanıcı izolasyonu, bulut sır saklama, üretim izlemesi ve SEO sitesi içermez. Yalnızca localhost'a bağlanır; ağda veya internette erişime açmayın. Gerçek para ile emir gönderecek modül, gerçek borsa filtreleri, kısmi gerçekleşme, belirsiz emir sonucu uzlaştırması ve kapsamlı strateji değerlendirmesi henüz geliştirilmedi.

## Resmî teknik kaynaklar

- [Binance Spot piyasa verisi](https://developers.binance.com/en/docs/catalog/core-trading-spot-trading/api/rest-api/market)
- [Binance Spot Testnet REST API, imza ve test emri](https://developers.binance.com/en/docs/products/spot/testnet/rest-api)

Ürün açıklaması: **Gerçek Binance fiyatlarıyla sanal işlem yapılabilen, risk ve portföy takibi sunan Python panel prototipi; kalıcı işlem defteri ve test ortamı emir doğrulama bağlantısı.**

## v0.3 incelemesi

Demoyu yeniden oynatma eski denemeyi demo_archive klasöründe korur. Aynı çalışma alanında ikinci panel engellenir. Yavaş bağlantı sırasında duraklatma yanıt verebilir; bekleyen fiyat, ayar değişiminden sonra uygulanmaz. Güncel veri yoksa risk kartı Veri bekleniyor gösterir. Ayrıntılı sonuçlar GUVENLIK-VE-KALITE-RAPORU.md içinde; CV metni ve iki dakikalık gösterim PORTFOY-SUNUMU.md içindedir.
