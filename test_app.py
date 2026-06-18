from app.crawler.extractor import auto_extract_product_details
from app.matching.normalizer import normalize_product_name
from app.matching.model_extractor import extract_model

def test_extractors():
    print("Testing auto_extract_product_details...")
    html_mock = """
    <html>
      <head>
        <title>Bếp từ Bosch PID675DC1E chính hãng giá rẻ</title>
        <meta property="og:title" content="Bếp từ Bosch PID675DC1E chính hãng" />
        <meta property="og:image" content="https://bep365.vn/images/pid675dc1e.jpg" />
        <script type="application/ld+json">
        {
          "@context": "https://schema.org",
          "@type": "Product",
          "name": "Bếp từ Bosch PID675DC1E Series 8 nhập khẩu Đức",
          "brand": { "@type": "Brand", "name": "Bosch" },
          "sku": "PID675DC1E",
          "offers": {
            "@type": "Offer",
            "price": "12.990.000 đ",
            "priceCurrency": "VND"
          }
        }
        </script>
      </head>
      <body>
        <h1>Bếp từ Bosch PID675DC1E chính hãng giá tốt</h1>
      </body>
    </html>
    """
    
    extracted = auto_extract_product_details(html_mock, "https://bep365.vn/bep-dien-tu-bosch.html")
    assert extracted["name"] == "Bếp từ Bosch PID675DC1E Series 8 nhập khẩu Đức", f"Got {extracted['name']}"
    assert extracted["brand"] == "Bosch", f"Got {extracted['brand']}"
    assert extracted["model"] == "PID675DC1E", f"Got {extracted['model']}"
    assert extracted["final_price"] == 12990000.0, f"Got {extracted['final_price']}"
    assert extracted["image"] == "https://bep365.vn/images/pid675dc1e.jpg", f"Got {extracted['image']}"
    print("Extractors test passed!")

def test_normalizer():
    print("Testing Normalizer & Model extraction...")
    norm1 = normalize_product_name("Bếp từ Bosch PID675DC1E chính hãng")
    norm2 = normalize_product_name("Bếp điện từ Bosch PID675DC1E nhập khẩu Đức")
    
    print(f"Norm 1: {norm1}")
    print(f"Norm 2: {norm2}")
    
    # Models should extract and match
    m1 = extract_model("Bếp điện từ Bosch PID675DC1E chính hãng")
    m2 = extract_model("Bếp từ Bosch PID675DC1E Series 8")
    
    assert m1 == "PID675DC1E", f"Got {m1}"
    assert m2 == "PID675DC1E", f"Got {m2}"
    print("Normalizer and Model extraction test passed!")

if __name__ == "__main__":
    test_extractors()
    test_normalizer()
    print("All tests completed successfully!")
