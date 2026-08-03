import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).parent
STORE_PAGE = PROJECT_ROOT / "site" / "src" / "pages" / "store" / "index.astro"
HOME_PAGE = PROJECT_ROOT / "site" / "src" / "pages" / "index.astro"
SIGN_ASSET = (
    PROJECT_ROOT
    / "site"
    / "public"
    / "assets"
    / "merchandise"
    / "face-forward-parking-sign-12x18.webp"
)

PRODUCT_URLS = {
    "mug": "https://face-forward-shop.fourthwall.com/products/face-forward-black-glossy-mug",
    "t-shirt": "https://face-forward-shop.fourthwall.com/products/face-forward-t-shirt",
    "bumper sticker": "https://face-forward-shop.fourthwall.com/products/face-forward-bumper-sticker",
}

PRODUCT_ASSETS = {
    "t-shirt": "/assets/merchandise/face-forward-t-shirt.webp",
    "bumper sticker": "/assets/merchandise/face-forward-bumper-sticker.webp",
    "mug": "/assets/merchandise/face-forward-black-glossy-mug.webp",
}

PRODUCT_LINK_VARIABLES = {
    "t-shirt": "teeUrl",
    "bumper sticker": "stickerUrl",
    "mug": "mugUrl",
}

INTERNAL_MERCHANDISE_COPY = (
    "without turning a small idea into a sprawling catalog",
    "Three objects. Then stop.",
    "The sellable collection stops there",
    "not a fourth product to fulfill",
    "too good to leave in the design folder",
    "First edition discipline",
    "A collection, not a catalog.",
)


def test_store_lists_the_three_fourthwall_products_without_placeholder_copy():
    page = STORE_PAGE.read_text()

    for product_name, product_url in PRODUCT_URLS.items():
        assert product_url in page, f"Missing Fourthwall link for {product_name}"

    assert "Not a store. Not yet." not in page
    assert "Placeholder" not in page

    for phrase in INTERNAL_MERCHANDISE_COPY:
        assert phrase not in page


def test_real_product_images_link_directly_to_each_fourthwall_listing():
    store_page = STORE_PAGE.read_text()
    home_page = HOME_PAGE.read_text()

    for product_name, public_path in PRODUCT_ASSETS.items():
        asset_path = PROJECT_ROOT / "site" / "public" / public_path.removeprefix("/")
        assert asset_path.exists(), f"Missing optimized image for {product_name}"

        link_variable = PRODUCT_LINK_VARIABLES[product_name]
        linked_image = re.compile(
            rf'<a[^>]+href=\{{{link_variable}\}}[^>]*>.*?'
            rf'<img[^>]+src="{re.escape(public_path)}"',
            re.DOTALL,
        )
        assert linked_image.search(store_page), (
            f"The {product_name} image must link directly to its Fourthwall listing"
        )
        assert linked_image.search(home_page), (
            f"The homepage {product_name} image must link directly to its Fourthwall listing"
        )


def test_store_displays_the_parking_sign_as_a_not_for_sale_artifact():
    page = STORE_PAGE.read_text()

    assert SIGN_ASSET.exists()
    assert "/assets/merchandise/face-forward-parking-sign-12x18.webp" in page
    assert "Not for sale" in page
    assert "campaign object" in page.lower()
    assert "Face Forward Only parking sign" in page


def test_store_closing_cta_links_to_the_manifesto_not_the_storefront_root():
    page = STORE_PAGE.read_text()

    assert 'href="/manifesto/"' in page
    assert "Understand our position" in page
    assert 'href="https://face-forward-shop.fourthwall.com/"' not in page
    assert "Shop on Fourthwall" not in page


def test_no_em_dashes_anywhere_in_visitor_facing_source():
    """Em dashes read as an AI tell; the site copy must stay free of them."""
    pages_dir = PROJECT_ROOT / "site" / "src"
    offenders = []
    for astro_file in pages_dir.rglob("*.astro"):
        text = astro_file.read_text()
        if "\u2014" in text or "&mdash;" in text or "&#8212;" in text:
            offenders.append(str(astro_file.relative_to(PROJECT_ROOT)))
    assert not offenders, f"Em dashes found in: {offenders}"


def test_homepage_introduces_the_current_first_edition_collection():
    page = HOME_PAGE.read_text()

    assert "Spread the word." in page
    assert (
        "Purchase merchandise online and show your friends, neighbors and coparkers "
        "why you face facts. Face forward."
    ) in page
    assert "Wear the position lightly." not in page
    assert "The Double-F mark on a black T-shirt, bumper sticker, and glossy mug." not in page
    assert "One bumper sticker. One wearable. One sign for the lot." not in page
    assert "Placeholder product rendering" not in page
    assert "Shop the First Edition" in page

    for phrase in INTERNAL_MERCHANDISE_COPY:
        assert phrase not in page
