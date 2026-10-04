# TradeIQ

Türkçe arayüzlü, yerel çalışan işlem simülasyonu ve piyasa takip paneli.

## Özellikler

- Kurgusal verilerle çevrimdışı demo.
- Binance halka açık fiyatlarıyla sanal alım/satım; SOL, BTC ve ETH.
- Kalıcı işlem muhasebesi, komisyon, kayma, zarar sınırları ve CSV dışa aktarma.
- Binance Spot Testnet hesap bağlantısı ve `/order/test` doğrulaması.
- Mobil ekranlara uyumlu panel ve 61 otomatik test.

## Çalıştırma

Python 3.10 veya üzeri gerekir. Ek paket kurulumu gerekmez.

```sh
python dashboard.py
```

Tarayıcıda http://127.0.0.1:8765 adresini açın. Windows üzerinde `Paneli-Ac.cmd` de kullanılabilir.

```sh
python -m unittest discover -v
python simulator.py --output results/demo
```

## Sınırlar

Bu sürüm portföy ve geliştirme amaçlı bir prototiptir. Gerçek parayla emir göndermez. Testnet `/order/test` çağrısı gerçek veya testnet üzerinde gerçekleşen bir emir oluşturmaz. API anahtarları yalnızca çalışan süreç belleğinde tutulur; depoya anahtar eklemeyin.

Panel yalnızca yerel kullanım için tasarlanmıştır. İnternete açık ücretli hizmet olarak sunulmadan önce sunucu altyapısı, kullanıcı kimlik doğrulaması ve bağımsız güvenlik incelemesi gerekir. GitHub Pages Python sunucusunu çalıştıramaz. Örnek sonuçlar kârlılık kanıtı değildir.

Testnet hesabıyla uçtan uca doğrulama henüz yapılmamıştır. 61 testin geçmesi kusursuzluk veya güvenlik sertifikası anlamına gelmez.

[Kullanım kılavuzu](PANEL-KILAVUZU.md) · [Güvenlik ve kalite raporu](GUVENLIK-VE-KALITE-RAPORU.md)

Proje, AI destekli geliştirme ve doğrulama süreciyle hazırlanmıştır.
