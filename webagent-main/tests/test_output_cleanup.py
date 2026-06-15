from main import _clear_previous_run_images


def test_clear_previous_run_images_keeps_non_image_outputs(tmp_path):
    output_dir = tmp_path / "output"
    nested_dir = output_dir / "screenshots"
    nested_dir.mkdir(parents=True)

    image = output_dir / "TS001_step_1.png"
    nested_image = nested_dir / "TS002_step_1.jpg"
    pdf = output_dir / "TS001_evidence.pdf"
    report = output_dir / "report_20260607.html"
    data = output_dir / "test_cases_manual1.json"
    log = output_dir / "runtime.log"

    for path in [image, nested_image, pdf, report, data, log]:
        path.write_bytes(b"x")

    deleted = _clear_previous_run_images(str(output_dir))

    assert deleted == 2
    assert not image.exists()
    assert not nested_image.exists()
    assert pdf.exists()
    assert report.exists()
    assert data.exists()
    assert log.exists()
