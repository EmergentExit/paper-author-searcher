# Benchmark stratified_researchers_v1 — run sibling-chg

- Gate: **failed** (exit code 1)
- Aggregation: macro over people; minimum 2 people per stratum
- Thresholds: identity_precision (macro) ≥ 100%, verified_paper_recall (macro) ≥ 80%, verified_paper_recall (micro) ≥ 80%, author_match_precision (macro) ≥ 95%, author_match_precision (micro) ≥ 95%
- Truth: 0 human-reviewed, 32 assistant-reviewed, 0 provisional, 0 missing
- Truth floor (people below it are left out of that metric): verified_paper_recall ≥ 3 publications, paper_discovery_recall ≥ 3 publications, actual_download_coverage ≥ 3 publications
- 1 of 55 stratum checks failed
- 32 people are scored against truth that is not human-reviewed

## Strata

| Stratum | People | identity_resolution_rate | identity_precision | verified_paper_recall | paper_discovery_recall | author_match_precision | actual_download_coverage |
|---|---:|---:|---:|---:|---:|---:|---:|
| overall=all | 32 | 90.6% (min 0.0%) | 100.0% (min 100.0%) | 92.1% (min 0.0%) · micro 90.2% | 96.1% (min 73.3%) | 99.6% (min 90.9%) · micro 99.7% | – (min –) |
| seniority=renowned_senior | 8 | 87.5% (min 0.0%) | 100.0% (min 100.0%) | 83.2% (min 0.0%) · micro 83.6% | 92.5% (min 73.3%) | 98.4% (min 90.9%) · micro 99.1% | – (min –) |
| seniority=established_faculty | 8 | 100.0% (min 100.0%) | 100.0% (min 100.0%) | 93.3% (min 81.6%) · micro 92.9% | 96.3% (min 86.8%) | 100.0% (min 100.0%) · micro 100.0% | – (min –) |
| seniority=assistant_professor | 8 | 87.5% (min 0.0%) | 100.0% (min 100.0%) | 95.6% (min 87.0%) · micro 95.1% | 98.1% (min 93.9%) | 100.0% (min 100.0%) · micro 100.0% | – (min –) |
| seniority=phd_student | 8 | 87.5% (min 0.0%) | 100.0% (min 100.0%) | 98.3% (min 90.0%) · micro 98.3% | 98.3% (min 90.0%) | 100.0% (min 100.0%) · micro 100.0% | – (min –) |
| field=computer_science | 8 | 100.0% (min 100.0%) | 100.0% (min 100.0%) | 93.1% (min 81.6%) · micro 92.6% | 94.5% (min 86.8%) | 100.0% (min 100.0%) · micro 100.0% | – (min –) |
| field=biomedicine | 8 | 87.5% (min 0.0%) | 100.0% (min 100.0%) | 92.3% (min 87.0%) · micro 92.2% | 95.0% (min 87.0%) | 98.4% (min 90.9%) · micro 98.6% | – (min –) |
| field=engineering | 8 | 100.0% (min 100.0%) | 100.0% (min 100.0%) | 97.9% (min 93.3%) · micro 98.8% | 99.8% (min 98.5%) | 100.0% (min 100.0%) · micro 100.0% | – (min –) |
| field=social_science | 8 | 75.0% (min 0.0%) | 100.0% (min 100.0%) | 84.9% (min 0.0%) · micro 60.0% ✗ | 95.4% (min 73.3%) | 100.0% (min 100.0%) · micro 100.0% | – (min –) |
| name_profile=common_chinese | 16 | 81.2% (min 0.0%) | 100.0% (min 100.0%) | 87.1% (min 0.0%) · micro 83.5% | 94.5% (min 73.3%) | 99.3% (min 90.9%) · micro 99.5% | – (min –) |
| name_profile=other | 16 | 100.0% (min 100.0%) | 100.0% (min 100.0%) | 96.8% (min 87.0%) · micro 96.5% | 97.6% (min 87.0%) | 99.8% (min 97.6%) · micro 99.8% | – (min –) |

## People

| Person | Strata | Truth | Run | Positives | identity_resolution_rate | identity_precision | verified_paper_recall | paper_discovery_recall | author_match_precision | actual_download_coverage |
|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| cs_wei_wang_ucla | renowned_senior, computer_science, common_chinese | assistant_reviewed | partial | 86 | 100.0% (1/1) | 100.0% (1/1) | 91.9% (79/86) | 93.0% (80/86) | 100.0% (79/79) | – (0/86) |
| cs_christopher_manning | renowned_senior, computer_science, other | assistant_reviewed | partial | 35 | 100.0% (1/1) | 100.0% (1/1) | 91.4% (32/35) | 91.4% (32/35) | 100.0% (32/32) | – (0/35) |
| cs_yang_liu_tsinghua | established_faculty, computer_science, common_chinese | assistant_reviewed | partial | 76 | 100.0% (1/1) | 100.0% (1/1) | 81.6% (62/76) | 86.8% (66/76) | 100.0% (62/62) | – (0/76) |
| cs_graham_neubig | established_faculty, computer_science, other | assistant_reviewed | completed | 97 | 100.0% (1/1) | 100.0% (1/1) | 100.0% (97/97) | 100.0% (97/97) | 100.0% (97/97) | – (0/97) |
| cs_huan_zhang | assistant_professor, computer_science, common_chinese | assistant_reviewed | partial | 67 | 100.0% (1/1) | 100.0% (1/1) | 92.5% (62/67) | 97.0% (65/67) | 100.0% (62/62) | – (0/67) |
| cs_kevin_ellis | assistant_professor, computer_science, other | assistant_reviewed | partial | 41 | 100.0% (1/1) | 100.0% (1/1) | 97.6% (40/41) | 97.6% (40/41) | 100.0% (40/40) | – (0/41) |
| cs_jing_huang | phd_student, computer_science, common_chinese | assistant_reviewed | partial | 10 | 100.0% (1/1) | 100.0% (1/1) | 90.0% (9/10) | 90.0% (9/10) | 100.0% (9/9) | – (0/10) |
| cs_julie_kallini | phd_student, computer_science, other | assistant_reviewed | completed | 7 | 100.0% (1/1) | 100.0% (1/1) | 100.0% (7/7) | 100.0% (7/7) | 100.0% (7/7) | – (0/7) |
| bio_feng_zhang | renowned_senior, biomedicine, common_chinese | assistant_reviewed | partial | 21 | 100.0% (1/1) | 100.0% (1/1) | 95.2% (20/21) | 95.2% (20/21) | 90.9% (20/22) | – (0/21) |
| bio_jennifer_doudna | renowned_senior, biomedicine, other | assistant_reviewed | partial | 46 | 100.0% (1/1) | 100.0% (1/1) | 87.0% (40/46) | 87.0% (40/46) | 97.6% (40/41) | – (0/46) |
| bio_le_cong | established_faculty, biomedicine, common_chinese | assistant_reviewed | partial | 18 | 100.0% (1/1) | 100.0% (1/1) | 88.9% (16/18) | 88.9% (16/18) | 100.0% (16/16) | – (0/18) |
| bio_anshul_kundaje | established_faculty, biomedicine, other | assistant_reviewed | partial | 68 | 100.0% (1/1) | 100.0% (1/1) | 94.1% (64/68) | 100.0% (68/68) | 100.0% (64/64) | – (0/68) |
| bio_jia_liu | assistant_professor, biomedicine, common_chinese | assistant_reviewed | partial | 23 | 100.0% (1/1) | 100.0% (1/1) | 87.0% (20/23) | 100.0% (23/23) | 100.0% (20/20) | – (0/23) |
| bio_brian_hie | assistant_professor, biomedicine, other | assistant_reviewed | completed | 33 | 100.0% (1/1) | 100.0% (1/1) | 93.9% (31/33) | 93.9% (31/33) | 100.0% (31/31) | – (0/33) |
| bio_zhiwei_xue | phd_student, biomedicine, common_chinese | assistant_reviewed | partial | 1 | 0.0% (0/1) | – (0/0) | 0.0% (0/1) | 0.0% (0/1) | – (0/0) | – (0/1) |
| bio_marena_trinidad | phd_student, biomedicine, other | assistant_reviewed | partial | 21 | 100.0% (1/1) | 100.0% (1/1) | 100.0% (21/21) | 100.0% (21/21) | 100.0% (21/21) | – (0/21) |
| eng_yi_cui | renowned_senior, engineering, common_chinese | assistant_reviewed | partial | 33 | 100.0% (1/1) | 100.0% (1/1) | 100.0% (33/33) | 100.0% (33/33) | 100.0% (33/33) | – (0/33) |
| eng_daniela_rus | renowned_senior, engineering, other | assistant_reviewed | completed | 94 | 100.0% (1/1) | 100.0% (1/1) | 100.0% (94/94) | 100.0% (94/94) | 100.0% (94/94) | – (0/94) |
| eng_wei_lu | established_faculty, engineering, common_chinese | assistant_reviewed | partial | 16 | 100.0% (1/1) | 100.0% (1/1) | 93.8% (15/16) | 100.0% (16/16) | 100.0% (15/15) | – (0/16) |
| eng_sindy_tang | established_faculty, engineering, other | assistant_reviewed | partial | 15 | 100.0% (1/1) | 100.0% (1/1) | 93.3% (14/15) | 100.0% (15/15) | 100.0% (14/14) | – (0/15) |
| eng_lu_lu | assistant_professor, engineering, common_chinese | assistant_reviewed | partial | 66 | 100.0% (1/1) | 100.0% (1/1) | 98.5% (65/66) | 98.5% (65/66) | 100.0% (65/65) | – (0/66) |
| eng_siddharth_krishnan | assistant_professor, engineering, other | assistant_reviewed | partial | 9 | 100.0% (1/1) | 100.0% (1/1) | 100.0% (9/9) | 100.0% (9/9) | 100.0% (9/9) | – (0/9) |
| eng_min_zhu | phd_student, engineering, common_chinese | assistant_reviewed | partial | 8 | 100.0% (1/1) | 100.0% (1/1) | 100.0% (8/8) | 100.0% (8/8) | 100.0% (8/8) | – (0/8) |
| eng_jonathan_lee | phd_student, engineering, other | assistant_reviewed | partial | 1 | 100.0% (1/1) | 100.0% (1/1) | 100.0% (1/1) | 100.0% (1/1) | 100.0% (1/1) | – (0/1) |
| soc_raj_chetty | renowned_senior, social_science, other | assistant_reviewed | partial | 19 | 100.0% (1/1) | 100.0% (1/1) | 100.0% (19/19) | 100.0% (19/19) | 100.0% (19/19) | – (0/19) |
| soc_yu_xie | renowned_senior, social_science, common_chinese | assistant_reviewed | partial | 45 | 0.0% (0/1) | – (0/0) | 0.0% (0/45) | 73.3% (33/45) | – (0/0) | – (0/45) |
| soc_xiang_zhou | established_faculty, social_science, common_chinese | assistant_reviewed | partial | 14 | 100.0% (1/1) | 100.0% (1/1) | 100.0% (14/14) | 100.0% (14/14) | 100.0% (14/14) | – (0/14) |
| soc_christian_wolf | established_faculty, social_science, other | assistant_reviewed | partial | 18 | 100.0% (1/1) | 100.0% (1/1) | 94.4% (17/18) | 94.4% (17/18) | 100.0% (17/17) | – (0/18) |
| soc_daniel_chen | assistant_professor, social_science, common_chinese | assistant_reviewed | partial | 2 | 0.0% (0/1) | – (0/0) | 0.0% (0/2) | 50.0% (1/2) | – (0/0) | – (0/2) |
| soc_nina_roussille | assistant_professor, social_science, other | assistant_reviewed | completed | 7 | 100.0% (1/1) | 100.0% (1/1) | 100.0% (7/7) | 100.0% (7/7) | 100.0% (7/7) | – (0/7) |
| soc_whitney_zhang | phd_student, social_science, common_chinese | assistant_reviewed | partial | 3 | 100.0% (1/1) | 100.0% (1/1) | 100.0% (3/3) | 100.0% (3/3) | 100.0% (3/3) | – (0/3) |
| soc_shakked_noy | phd_student, social_science, other | assistant_reviewed | partial | 9 | 100.0% (1/1) | 100.0% (1/1) | 100.0% (9/9) | 100.0% (9/9) | 100.0% (9/9) | – (0/9) |

Provisional truth uses proposed labels from researcher-maintained sources and is not a release certification. Precision under provisional truth treats the source union as complete and is a lower bound.
