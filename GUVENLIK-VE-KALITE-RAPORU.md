# TradeIQ v0.3 — güvenlik, doğruluk ve sunum incelemesi

İnceleme: 4 Ekim 2026. Kapsam: bu sohbetin çalışma kopyasındaki Python simülasyon motoru, yerel panel, Binance bağlantı katmanı, JavaScript arayüzü, kayıtlar ve çalıştırma dosyaları. Masaüstündeki orijinal bot değişmedi. Bu çalışma kod incelemesi ve hedefli testlerden oluşur; bağımsız sızma testi veya güvenlik sertifikası değildir.

## Sonuç

**Yerel ve tek kullanıcılı portföy demosu için kullanılabilir. Kamuya açık, abonelikli veya gerçek parayla işlem yapan hizmet için hazır değildir.** Taranan akışlarda gerçek para emri veya para çekme uç noktası bulunmadı. Üretim alan adına emir gönderen yol, bağlantı adresi izin listesiyle reddediliyor. Kullanıcı test anahtarları verilmediği için testnet hesap doğrulaması gerçek kimlikle denenmedi.

61 otomatik test geçti. Güvenlik incelemesinde bulunan sorunlar giderildi ve tekrar ortaya çıkmalarını yakalayacak testler eklendi. Testler tüm olası saldırıları veya çalışma koşullarını kapsamaz.

## Bulunan ve düzeltilen sorunlar

| Bulgu | Etki | Yapılan düzeltme ve kanıt |
|---|---|---|
| Ağ isteği bütün uygulama kilidini tutuyordu | Yavaş bağlantıda durum ekranı/duraklatma bekliyordu | Ağ ve kayıt kilitleri ayrıldı. Bloke edilen istek sırasında duraklatma ve durum sorgusu testte 0,5 saniye altında tamamlandı. Bu yerel test eşiğidir; genel gecikme garantisi değildir. |
| Bekleyen fiyat cevabı ayar değişiminden sonra uygulanabilirdi | Yanlış parite veya duraklatma sonrası işlem riski | Ayar nesli kontrolü; geç gelen cevap iptal edilir. Hem parite değişimi hem otomatik işlem sırasında duraklatma test edildi. |
| Aynı kayıtla iki süreç açılabiliyordu | Kayıt üstüne yazma ve muhasebe kaybı | Panel başlangıcında işletim sistemi dosya kilidi. İkinci yazıcı reddediliyor; ilk kapatılınca kilit açılıyor. CLI motorunu panelin runtime dosyası üzerinde ayrıca çalıştırmayın. |
| Bozuk sayı ve borsa yanıtları yeterince sınırlandırılmıyordu | Hesaplama hatası veya arka plan döngüsünün sona ermesi | Sayısal boyut/üs sınırları, veri biçimi hatalarında güvenli durma, yanıt boyutu sınırı. Aşırı üs, NaN, Infinity ve hatalı borsa nesneleri test edildi. |
| Bozuk/tekrarlı istek alanları kabul edilebiliyordu | Belirsiz komut davranışı ve bazı hatalarda sunucu istisnası | Tek Host/token/Content-Length, ASCII token, JSON alan tekrarının reddi, istek boyutu/çerçeve kontrolü, komut türü kontrolü. |
| Sunucu istek sayısı sınırsızdı | Yerel kaynak tüketimi | En çok 16 eşzamanlı istek ve soket zaman aşımı. Bu internet düzeyinde DDoS koruması değildir. |
| Test hesabının işlem izni kapalı olsa da doğrulama düğmesi açık kalıyordu | Başarısız isteğe yönlendirme | Hem arayüzde hem sunucuda izin kontrolü. İzin kapalıyken doğrulama isteği gönderilmediği test edildi. |
| Açık pozisyon yokken satış / açık pozisyona ikinci alım sessizce bekleme oluyordu | Kullanıcı işlemin gerçekleşip gerçekleşmediğini anlayamıyordu | Açıklayıcı hata; düğmeler uygun duruma göre kapanır. |
| Otomatik deneme seçimi yenilemede sıfırlanıyordu | Kullanıcının seçimi kaybolabiliyordu | Kullanıcının henüz uygulanmamış seçimi korunur; takip açıkken ayar değişmez. Tarayıcıda doğrulandı. |
| Son 200 olay grafiği yapay 1000 değerine bağlanıyordu | Uzun kaydın hareketi yanıltıcı görünüyordu | Grafik yalnız görünür dönemi çizer ve dönemi açıkça etiketler. |
| Eski fiyatla “Sınırlar içinde” ifadesi gösteriliyordu | Korumanın aktif olduğu izlenimi | Veri yok/eski/hatalıysa “Veri bekleniyor”; koruma değerlendirmesinin yapılamadığı açıklanır. |
| Demo bitince yeniden başlatmak teknik işlem istiyordu | Görüşme veya kullanıcı sunumunda sürtünme | “Demoyu yeniden oynat” düğmesi; eski kayıt ayrı arşivde korunur. |

## Kontrol edilen güvenlik önlemleri

- Sadece `127.0.0.1` üzerinde dinleme; izinli Host/Origin, uygulama token'ı ve çapraz site kontrolü. Token bir kullanıcı hesabı/parola sistemi değildir; aynı bilgisayardaki zararlı yazılıma karşı koruma sağlamaz.
- Tarayıcı güvenlik başlıkları: CSP, çerçeveleme engeli, içerik türü kontrolü, referrer kısıtı, kamera/mikrofon/konum izinlerinin kapatılması.
- API cevaplarında gizli anahtar yok; anahtarlar kayıt ve raporlara eklenmiyor. İstek günlükleri kapalı. Anahtarlar sadece testnet'e gönderilebilir; yönlendirmeler reddedilir. TLS doğrulaması kapatılmadı.
- Dosya servisi sabit izin listesine sahip. `.env`, kayıt ve kaynak dosyası yolları HTTP üzerinden servis edilmiyor. Yol atlama denemeleri test edildi.
- Arayüz verileri textContent ile yazılıyor; kullanıcı/borsa metni HTML olarak çalıştırılmıyor. CSV ihracında formül başlangıcı olabilen metinler kaçırılıyor.
- Bakiye, pozisyon, ücret ve gerçekleşen kâr/zarar defteri uzlaştırılıyor. Hatalı kayıt sessizce sıfırlanmıyor. Olay kaydı ve muhasebe atomik checkpoint'te tutuluyor.

## Kalan sınırlar ve satış öncesi işler

Yerel Python sunucusu kamuya açılmamalıdır. Python'un resmî belgeleri `http.server` için üretim kullanımını önermiyor. İnternette bir hizmet sunulacaksa üretim sunucusu, HTTPS, kullanıcı kimliği ve oturum yönetimi, hesap izolasyonu, erişim kontrolü, güvenli sır saklama, oran sınırlama ve izleme ayrıca gerekir. [Python belgeleri](https://docs.python.org/3/library/http.server.html)

Testnet gerçek anahtar bağlantısı henüz doğrulanmadı. Test emir doğrulaması eşleştirme motoruna emir göndermez. Gerçek para kullanımından önce borsa filtreleri, kısmi gerçekleşme, belirsiz emir sonucu, tekrar gönderim önleme, borsa hesabıyla uzlaştırma ve gerçek işlem ücretleri gerekir. Bu inceleme stratejinin kârlılığını doğrulamaz.

Kayıtlar bu bilgisayarda düz JSON'dur; bütünlük kontrolleri muhasebe hatalarını bulur, kötü amaçlı kişinin dosyayı tutarlı biçimde değiştirmesine karşı imza sağlamaz. Anahtar belleği güvenli sıfırlama garantisi yoktur. Dış servis kesilince stop çalışamaz; uygulama kapalıyken takip yoktur. Ağ yanıtı bekleyen test doğrulaması bağlantı kaldırmayla eşzamanlıysa önceden gönderilmiş isteğin dış sistemden geri alınması garanti edilmez; zaten bu uç nokta gerçekleşen emir üretmez.

Ücretli/çok kullanıcılı sürüm için bu maddelerin tamamı ayrı kabul kriterlerine bağlanmalıdır. Önce küçük bir deneme grubu ile kurulum, anlaşılabilirlik ve hata mesajları sınanmalıdır. Bu teslimde bağımsız kullanıcı araştırması yapılmadı; insanların beğeneceği doğrulanmış sayılmaz.

## CV ve portföy değerlendirmesi

Projenin kanıtlanabilir değeri: çalışan arayüz, gerçek piyasa verisi, ücretli sanal muhasebe, risk kontrolleri, kalıcı kayıt, bağlantı hatalarını yönetme ve 61 otomatik test. Görüşmede bu özellikleri açıklayabilmek, yalnızca ekran görüntüsü göstermekten daha güçlü bir kanıt oluşturur.

“AI ile para kazandıran bot”, “canlı kullanıma hazır”, “güvenlik açığı yok” veya “Binance hesabıyla tam entegre” ifadelerini kullanmayın. Gerçek fiyat bağlantısı ile testnet hesap doğrulamasını ayrı anlatın. AI destekli geliştirme sürecini yönettiğinizi belirtmek, kendi katkınızı doğru yansıtır.

Önerilen CV metni ve sunum akışı `PORTFOY-SUNUMU.md` içindedir.

## Test kanıtı ve kaynaklar

`results/review-test-results.txt`: 61 test, başarılı. Önceki 40 testin üzerine 21 güvenlik/yarış durumu/kullanılabilirlik testi eklendi. `results/public-connection-check.json` ve `results/public-paper-example.json`: önceki gerçek fiyat bağlantısı ve yerel sanal işlem kontrolü; strateji değerlendirmesi değildir.

CSRF token ve Origin yaklaşımı için [OWASP açıklaması](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html) incelendi. Buna dayanarak “OWASP sertifikalı” veya tam standart uyumu iddia edilmiyor.
