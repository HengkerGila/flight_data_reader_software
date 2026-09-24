# AFDA probe database

`afda_probe_64wps.adb` is a small dataframe written by this project in the
AFDA layout (`tools/make_afda_probe.py` regenerates it). It has the same
header as the real NC212i database (64 wps, standard sync words), so if it
fails to open the problem is in our parameter rows, not the header.

Every parameter is named after what it tests. Open the file in AFDA, look
at each parameter in AFDA's parameter editor (or its frame/word map if it
has one) and note what AFDA shows for subframe, word and bits. If AFDA can
**save** the database, save it under a new name and send that file back:
it shows how AFDA itself writes these rows, which is the best evidence.

## Checklist

| Parameter | What AFDA should show if our assumption is right | Write down |
| --- | --- | --- |
| PROBE A1 ABS W70 | SF2 word 6 (written as word 70, selector 1234) | subframe / word AFDA shows |
| PROBE A2 RATE4 W5 | word 5 in SF1, SF2, SF3, SF4 (4 samples) | how many samples, where |
| PROBE A3 SF24 W10 | word 10 in SF2 and SF4 only | subframes shown |
| PROBE B1 CONCAT MS W20 LS W21 | one value from two words; we listed word 21 (LS part) **first**, word 20 (MS part) second | which word AFDA labels MSB / first / high |
| PROBE B2 BCD SF4 W19 | SF4 word 19, digit weights 1 and 10 | subframe / word, weights if shown |
| PROBE C1 DISCRETE SF4 W56 B3 | SF4 word 56 bit 3, 0 = ON AIR, 1 = ON GROUND | as shown |
| PROBE C2 FOUR STATES W12 | four states OFF/LOW/MID/HIGH on bits 1-2 | accepted? all four labels visible? |
| PROBE D1 DECIMALS 3 | resolution 0.001, 3 decimals displayed | decimals shown |
| PROBE S1 SEL2 W6 | hand-written selector `2`, word 6 | SF2 word 6 = relative; SF1 word 6 = selector ignored |
| PROBE S2 SEL3 W70 | hand-written selector `3`, word 70 | SF2 word 6 = absolute; SF3 word 6 = modulo; SF4 word 6 = (3-1)×64+70 |
| PROBE S3 SEL13 W6 | hand-written selector `13`, word 6 | 2 samples (SF1, SF3) = mask; 1 sample = not a mask |

Also worth noting:

- Did the file open without any warning?
- If AFDA has a raw-data or replay view, does PROBE B1 read the two words
  in the order we assumed (word 20 high, word 21 low)?
- Anything AFDA changed when re-saving (number spelling, selector text,
  blank fields).

## What the answers change

- S1–S3 fix the rule in `adb_codec/parser.py` for selectors other than
  `1234` (today: relative when the word fits in one subframe, else
  absolute with the selector ignored).
- B1 fixes `mappings.PARTS_ORDER` (today LS-first, from the BCD weights of
  the vendor file).
- C2 and D1 confirm the state-table and decimals columns.
