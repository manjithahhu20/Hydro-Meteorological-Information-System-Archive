# HMIS Discharge Relations

HMIS does not publish an analytical river-rating equation through the public
API used by the dashboard. The public `rating-tables` endpoint publishes
stage-discharge tables for a subset of gauges. Those tables are piecewise
linear:

```text
Q = Q1 + (Q2 - Q1) * (H - H1) / (H2 - H1)
```

where `(H1,Q1)` and `(H2,Q2)` are adjacent stage-discharge points in
`ratings/rating_table_points.csv`, and `H` is water level in metres.

For gauges without a public rating table, `discharge_relations.csv`
contains an empirical fit to archived paired water-level/discharge
observations. Empirical fits are not official HMIS formulas and should not
be extrapolated outside the stated stage range.

## Summary

- Official named rating tables: 1
- Inferred legacy rating tables: 0
- Empirical fits only: 32
- No public relation or discharge data: 21
- Non-empty per-station analytical equation records: 0
- Generated at UTC: 2026-11-08 11:30:24

## Per-Gauge Relations

| Gauge | Relation | Stage range (m) | Equation | R2 | Pairs |
| --- | --- | --- | --- | --- | --- |
| 00001HD002 | no_public_relation | - | not available |  | 0 |
| 00009HD001 | no_public_relation | - | not available |  | 0 |
| 00022HD001 | no_public_relation | - | not available |  | 0 |
| Aththanagalu Oya_Dunamale | empirical_fit_from_archive | 0.300-4.830 | Q = -1.3857522 + 9.2477199*H^1 + -3.7610341*H^2 + 2.1571355*H^3 + -0.20159936*H^4 | 0.999493 | 2708 |
| Deduru Oya_Moragaswewa | empirical_fit_from_archive | -0.320-5.300 | Q = 1.4362065 + 14.126517*H^1 + 27.089262*H^2 + -5.2071806*H^3 + 0.48849615*H^4 | 0.998910 | 2998 |
| Gal Oya_Ampara | no_public_relation | - | not available |  | 0 |
| Gin Ganga_Baddegama | empirical_fit_from_archive | 0.000-4.830 | Q = 27.125622 * (H - (-0.23948133))^1.5931637 | 0.999515 | 2806 |
| Gin Ganga_Thawalama | empirical_fit_from_archive | 0.970-5.480 | Q = 27.677872 * (H - (-0.22482962))^1.5812101 | 0.999489 | 2794 |
| Heda Oya_Siyambalanduwa | empirical_fit_from_archive | 0.000-3.190 | Q = 0.5422786 + -5.7278194*H^1 + 17.217457*H^2 + -1.1844461*H^3 | 0.999690 | 2852 |
| IDP | no_public_relation | - | not available |  | 0 |
| IDQ | no_public_relation | - | not available |  | 0 |
| IDR | no_public_relation | - | not available |  | 0 |
| IDS | no_public_relation | - | not available |  | 0 |
| IDT | no_public_relation | - | not available |  | 0 |
| IDV | no_public_relation | - | not available |  | 0 |
| IDW | no_public_relation | - | not available |  | 0 |
| IDX | no_public_relation | - | not available |  | 0 |
| Kalu Ganga_Ellagawa | official_rating_table_piecewise_linear | -0.640-8.090 | Q = Q1 + (Q2 - Q1) * (H - H1) / (H2 - H1), where (H1,Q1) and (H2,Q2) are adjacent rating-table points | 0.719052 | 3742 |
| Kalu Ganga_Kalawellawa (Millakanda) | empirical_fit_from_archive | 1.220-7.860 | Q = 178.95558 + -245.50108*H^1 + 113.63504*H^2 + -16.232538*H^3 + 0.83035408*H^4 | 0.995043 | 2495 |
| Kalu Ganga_Magura | no_public_relation | - | not available |  | 0 |
| Kalu Ganga_Putupaula | empirical_fit_from_archive | 0.050-3.750 | Q = 6563.5335 + 2269.1201*H^1 + 195.73142*H^2 + -0.041795382*H^3 + 0.0011137316*H^4 | 1.000000 | 2560 |
| Kalu Ganga_Rathnapura | empirical_fit_from_archive | 0.470-8.680 | Q = 3.8779389 + 8.2137546*H^1 + 4.3614011*H^2 | 0.999921 | 2721 |
| Kelani Ganga_Deraniyagala | empirical_fit_from_archive | 0.076-6.240 | Q = 28.612789 * (H - (-0.00016332763))^1.3840754 | 1.000000 | 2857 |
| Kelani Ganga_Glencourse | empirical_fit_from_archive | 6.850-16.430 | Q = 16459.203 + -5710.1864*H^1 + 732.13298*H^2 + -41.003857*H^3 + 0.86096112*H^4 | 0.748295 | 4687 |
| Kelani Ganga_Hanwella | empirical_fit_from_archive | 0.470-7.170 | Q = 8.1229006 + 37.818233*H^1 + 9.7012334*H^2 + 0.31685348*H^3 | 0.998152 | 3272 |
| Kelani Ganga_Holombuwa | empirical_fit_from_archive | 0.160-5.190 | Q = 0.11952355 + -2.1588819*H^1 + 14.787572*H^2 + -0.74954463*H^3 | 0.994813 | 3257 |
| Kelani Ganga_Kithulgala | empirical_fit_from_archive | 1.140-7.500 | Q = -13.297835 + -13.515031*H^1 + 26.207133*H^2 + 0.36764043*H^3 | 0.999746 | 2881 |
| Kelani Ganga_Nagalagam Street | no_public_relation | - | not available |  | 0 |
| Kelani Ganga_Norwood | empirical_fit_from_archive | 0.055-3.420 | Q = 2.2350546 + -4.2908308*H^1 + 11.890738*H^2 + -0.14155965*H^3 + 0.14655543*H^4 | 0.981063 | 2796 |
| Kelani River_Karawanella Bridge | no_public_relation | - | not available |  | 0 |
| Kirindi Oya_Kuda Oya | empirical_fit_from_archive | 0.102-4.320 | Q = -15.753481 + 50.078675*H^1 + -56.924242*H^2 + 26.411424*H^3 + -3.3889418*H^4 | 0.903360 | 2708 |
| Kirindi Oya_Thanamalwila | empirical_fit_from_archive | -0.090-3.410 | Q = 0.66502177 + 2.6596692*H^1 + 22.272384*H^2 + -9.0852788*H^3 + 1.8529145*H^4 | 0.998115 | 2818 |
| Kirindi Oya_Wellawaya | empirical_fit_from_archive | 0.000-3.290 | Q = 25.035491 * (H - (0.00083470549))^1.6725173 | 0.994909 | 2830 |
| Kumbukkan Oya_Nakkala | empirical_fit_from_archive | 0.490-2.560 | Q = 0.79279037 + -7.037084*H^1 + 14.244341*H^2 + -1.5670714*H^3 + 0.18518852*H^4 | 1.000000 | 2799 |
| Ma Oya_Yaka Wewa | empirical_fit_from_archive | 0.590-1.190 | Q = 2 + 2*H^1 + -3.014353e-11*H^2 | 1.000000 | 136 |
| Maduru Oya_Padiyathalawa | empirical_fit_from_archive | 0.000-4.500 | Q = 0.1166727 + -2.237461*H^1 + 10.537816*H^2 + 1.7313234*H^3 + -0.18601533*H^4 | 0.999964 | 2607 |
| Maha Oya_Badalgama | empirical_fit_from_archive | 1.740-5.770 | Q = 0.83082275 * (H - (-1.3589362))^3.4775461 | 0.949497 | 3276 |
| Maha Oya_Giriulla | empirical_fit_from_archive | 0.157-5.180 | Q = 87.092012 + -214.6731*H^1 + 159.13861*H^2 + -34.259939*H^3 + 2.8433991*H^4 | 0.996031 | 2903 |
| Mahaweli Ganga_Manampitiya | empirical_fit_from_archive | -0.600-2.070 | Q = 28.608939 * (H - (-0.6006113))^2.3490733 | 0.993159 | 2692 |
| Mahaweli Ganga_Nawalapitiya | empirical_fit_from_archive | 0.510-8.070 | Q = 14.786368 * (H - (0.11620294))^1.9613273 | 0.889539 | 2722 |
| Mahaweli Ganga_Peradeniya | empirical_fit_from_archive | 1.150-9.730 | Q = -28.330643 + 29.640505*H^1 + 7.1938036*H^2 + 0.54339462*H^3 + -0.024708222*H^4 | 0.999833 | 2741 |
| Mahaweli Ganga_Thaldena | empirical_fit_from_archive | 0.020-1.910 | Q = 0.28403113 + 8.5810966*H^1 + -13.891495*H^2 + 26.819122*H^3 + -6.121802*H^4 | 0.999738 | 2723 |
| Mahaweli Ganga_Weraganthota | empirical_fit_from_archive | -3.930-3.200 | Q = 275.09481 + 171.09965*H^1 + 28.040609*H^2 + -0.58357418*H^3 + -0.27694461*H^4 | 0.999893 | 1490 |
| Mahaweli River_Mahiyanganaya Bridge | no_public_relation | - | not available |  | 0 |
| Malwathu Oya_Thanthirimale | empirical_fit_from_archive | 0.340-3.200 | Q = -1.0180115 + 1.899655*H^1 + 4.605953*H^2 | 0.988922 | 1618 |
| Mee Oya_Galgamuwa | empirical_fit_from_archive | 0.000-4.230 | Q = 0.025006448 + 0.57538399*H^1 + 3.6587521*H^2 | 0.996395 | 1154 |
| Menik Ganga_Katharagama | empirical_fit_from_archive | -0.300-0.420 | Q = 5.582359 + 5.7944893*H^1 + 1.4824718*H^2 + -0.0072604761*H^3 + 0.00098913362*H^4 | 1.000000 | 2851 |
| Nilwala Ganga_Panadugama | no_public_relation | - | not available |  | 0 |
| Nilwala Ganga_Pitabeddara | no_public_relation | - | not available |  | 0 |
| Nilwala Ganga_Thalgahagoda | no_public_relation | - | not available |  | 0 |
| Nilwala Ganga_Urawa | no_public_relation | - | not available |  | 0 |
| Tisssa Wewa | no_public_relation | - | not available |  | 0 |
| Walawe Ganga_Moraketiya | empirical_fit_from_archive | 0.450-4.660 | Q = 25.471411 * (H - (0.1579589))^1.9239856 | 0.972858 | 2782 |
| Yan Oya_Horowpothana | empirical_fit_from_archive | 1.100-3.400 | Q = 6.6970119 + -15.580035*H^1 + 9.5496856*H^2 + -0.76115525*H^3 | 0.997183 | 2906 |

## Notes

- `official_rating_table_piecewise_linear` means HMIS named the gauge in
  its public rating table.
- `inferred_rating_table_piecewise_linear` means the rating-table station
  field was blank and the gauge was inferred by matching the archived
  water-level/discharge pairs.
- `empirical_fit_from_archive` is only a statistical approximation.
- Gauges with no discharge rows are left blank rather than assigned an
  invented relation.
