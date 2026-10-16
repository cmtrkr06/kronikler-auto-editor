from fetch_fixtures import file_name


def test_file_names_are_flat_and_stable():
    assert file_name("https://shiftandtrend.com/sanziman/dq200/") == "sanziman_dq200.html"
    assert file_name("https://shiftandtrend.com/") == "index.html"
    assert file_name("https://shiftandtrend.com/sitemap_index.xml") == "sitemap_index_xml.xml"
