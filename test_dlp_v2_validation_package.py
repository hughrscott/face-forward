from dlp.build_v2_validation_package import add_reviewer_weights


def test_add_reviewer_weights_combines_manifest_and_second_stage_sampling():
    manifest = [
        {"item_id": "A", "sampling_stratum": "positive:parking:forward", "sampling_weight": 2.0},
        {"item_id": "B", "sampling_stratum": "positive:parking:forward", "sampling_weight": 2.0},
        {"item_id": "C", "sampling_stratum": "positive:parking:forward", "sampling_weight": 2.0},
        {"item_id": "D", "sampling_stratum": "random_track", "sampling_weight": 10.0},
    ]
    subset = [dict(manifest[0]), dict(manifest[1]), dict(manifest[3])]

    weighted = add_reviewer_weights(subset, manifest)

    by_id = {item["item_id"]: item for item in weighted}
    assert by_id["A"]["review_population_count"] == 3
    assert by_id["A"]["review_sample_count"] == 2
    assert by_id["A"]["review_sampling_weight"] == 1.5
    assert by_id["A"]["analysis_weight"] == 3.0
    assert by_id["D"]["review_sampling_weight"] == 1.0
    assert by_id["D"]["analysis_weight"] == 10.0
