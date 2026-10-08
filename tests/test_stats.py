

def test_median_ci_halfwidth_pct():
    from r7.stats import median_ci_halfwidth_pct, runs_for_halfwidth
    assert median_ci_halfwidth_pct([1.0, 1.0]) is None
    assert median_ci_halfwidth_pct([1.0] * 5) == 0.0
    vals = [0.9, 1.0, 1.1, 1.0, 1.2, 0.8]
    hw = median_ci_halfwidth_pct(vals)
    assert hw is not None and hw > 0
    n = runs_for_halfwidth(vals, hw / 2)
    assert n is not None and n >= 4 * len(vals) - 1
    assert runs_for_halfwidth(vals, 0) is None
