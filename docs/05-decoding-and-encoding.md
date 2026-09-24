# 05 — Decoding and encoding

## The decoder pipeline

`arinc717_reader/decoder/parameter_decoder.py` implements spec §10–§18. For
every parameter, every occurrence and every subframe the occurrence is
recorded in:

```
1. resolve the occurrence's segments, sorted by sequence
2. check the segments agree on the subframe set
3. for each segment: check bit range (1..12) and word (1..WPS), read the word,
   extract the bit field
4. assemble the segments, sequence 1 first (most significant)
5. interpret the assembled field by canonical type
6. apply the linear conversion where the type has one
7. check the engineering range
8. produce an EngineeringValue with a complete DecodeTrace
```

The decoder never reads GUI widgets and never raises for a bad mapping: every
problem becomes a status on the produced value.

### Bit extraction

`decoder/bit_extractor.py`: `extract_bits(word_value, lsb, msb)` validates
the word (0..4095) and the bit numbers (1..12), normalises the order of `lsb`
and `msb`, and returns `(word >> (low − 1)) & mask`. Width is
`high − low + 1`.

### Segment assembly

`decoder/segment_assembler.py`: `assemble_segments([(value, width), …])`
shifts each part in, first part most significant, and returns the assembled
value and total width. `split_segments(value, widths)` is the exact inverse
used by the encoder, so the convention has one home.

### Interpretation by type

| Type | Decoded decimal | Engineering value |
| --- | --- | --- |
| `analog_signed` | `twos_complement(assembled, width)`: subtract 2^width when the top bit is set | `decimal × resolution + offset` |
| `analog_unsigned` | the assembled integer | `decimal × resolution + offset` |
| `bcd` | digits of 4 bits from the least significant end (a leading partial digit takes the remaining bits), each validated 0–9, read as a decimal number. **Weighted BCD:** when every segment carries a `bcd_weight`, each segment is one digit field and the decimal is the sum of digit × weight (an integer when the weights allow, else a float rounded to 10 places) | `decimal × resolution + offset` |
| `discrete` | the assembled field | the state table's label for that value when the parameter has a table (`state_label`); otherwise `true_state` when non-zero, `false_state` when zero; defaults `TRUE` / `FALSE` when a label is missing |
| `raw` | the assembled integer | the same integer |
| `unknown` | — | status `UNSUPPORTED_TYPE` |

Rules the code enforces deliberately:

- **Signedness comes from the dataframe**, never from the bit pattern.
- **BCD is never reinterpreted as binary.** A nibble above 9 gives
  `INVALID_BCD`, in plain and in weighted BCD alike.
- **BCD digit weights come from the dataframe.** AFDA files list one word
  part per digit with its decimal weight; the decoder multiplies instead of
  concatenating nibbles, so a two-part `10, 1` day-of-month and a
  three-part `10, 1, 0.1` value decode without any assumption about digit
  positions. A parameter with a weight on only some segments is decoded as
  plain BCD.
- **A discrete's meaning comes from its labels.** An active-low signal simply
  has `true_state = "NORMAL"` and `false_state = "WARNING"`; nothing assumes
  1 means active. A multi-bit discrete with a state table (`0 = OFF, 1 =
  LOW, 2 = MID, 3 = HIGH`) decodes to the table's label; a value missing
  from the table falls back to the two labels.
- **Conversion supports** positive and negative resolution and zero,
  positive and negative offset. Only `formula_type = "linear"` is supported;
  anything else gives `UNSUPPORTED_TYPE`.
- **Range check.** A numeric engineering value below `minimum` or above
  `maximum` gets `OUT_OF_RANGE` with a message; the value itself is still
  reported.

### Statuses

| Status | Produced when |
| --- | --- |
| `VALID` | Everything decoded and the value is in range (or no range is defined). |
| `INVALID_MAPPING` | No occurrences, no segments, segments disagree on subframes, a bit number outside 1..12, a subframe outside 1..4, or a word outside 1..WPS of the *frame*. |
| `INVALID_BCD` | A BCD digit above 9. |
| `OUT_OF_RANGE` | The engineering value violates the parameter's minimum or maximum. |
| `UNSUPPORTED_TYPE` | Canonical type `unknown`, or a non-linear formula. |
| `MISSING_DATA` | Reserved; not produced by the current decoder. |

### Worked examples (the spec's oracle values)

**Pitch attitude, signed.** SF1 word 004 holds `0xD54` = 3412 =
`110101010100`. The mapping is bits 12–3, ten bits: extracted
`1101010101` = 853. Signed interpretation: 853 − 1024 = −171. Conversion
with resolution 0.176 and offset 0: −171 × 0.176 = **−30.096 deg**.

**Flap position, offset.** Decoded decimal 2738, resolution 0.0062, offset
−1.6: 2738 × 0.0062 − 1.6 = **15.3756 deg**.

**Aileron, negative resolution.** Resolution −0.0153, offset 31.2: raw 0
gives 31.2 deg, raw 4095 gives −31.45 deg.

**BCD selected course.** A 12-bit field `0011 0101 1001` splits into digits
3, 5, 9 → **359**. `0011 1011 0000` fails with `INVALID_BCD` (digit 11).

**Weighted BCD day of month** (the AFDA form). Two segments, sequence 1 in
word 19 bits 8–5 with weight 10 and sequence 2 in word 19 bits 4–1 with
weight 1. Extracted `0010` = 2 and `0111` = 7: 2 × 10 + 7 × 1 = **27**. With
weights `10, 1, 0.1` and digits 2, 7, 5 the decimal is 27.5 before the
linear conversion.

**Landing gear, discrete.** Bit 1 of word 13 with `true_state = "DOWN"`,
`false_state = "UP"`: a 1 decodes to **DOWN**.

**Four-state discrete.** Bits 2–1 of word 12 with the state table `0 = OFF,
1 = LOW, 2 = MID, 3 = HIGH`: `10` = 2 decodes to **MID**.

**Multi-segment altitude.** Word 154 bits 9–1 (`000000101` = 5) and word 153
bits 12–1 (`000011110000` = 240) assemble to `000000101000011110000`
(21 bits) = 5 × 4096 + 240 = 20720; with resolution 0.25 the altitude is
**5180 ft**.

## The encoder and closed-loop validation

`arinc717_reader/encoder/parameter_encoder.py` inverts the pipeline for the
scenario simulator (spec §24–§26):

```
engineering value
  → invert the linear conversion: (value − offset) / resolution, rounded
  → encode: two's complement / unsigned / BCD / discrete label / raw
  → split across the occurrence's segments (inverse of assembly)
  → write each part into its word, in every subframe of the segment
```

**Weighted BCD** takes its own path: the inverted value is divided by the
smallest weight and rounded to an integer, whose decimal digits are dealt
out one per segment, most significant first. The weights must form a
decade ladder in segment order (`…, 10, 1, 0.1`); anything else has no
unique digit split and is refused with `ENCODE_ERROR`, as is a value with
more digits than segments or a digit that does not fit a segment's width.

`encode_into_frame()` writes **every occurrence** by default so that any
sample decodes to the value. It returns the raw pattern written.
`build_scenario_frame()` starts from a blank frame with the dataframe's sync
words (or from a copy of an existing frame) and encodes a whole scenario,
returning the frame and the pattern per parameter.

`is_encodable()` decides which parameters the Scenario page offers: a known
type, at least one occurrence with segments, and for analog and BCD types a
linear conversion with a non-zero resolution.

`quantization_tolerance()` is half a resolution step for analog and BCD
types (plus a tiny epsilon) and zero for discretes and raw values. The
simulation service compares the requested value with the first valid decoded
sample and reports PASS when the difference is within that tolerance.

**Example.** Requested pitch −20 deg: (−20 − 0) / 0.176 = −113.6 → −114;
ten-bit two's complement `1110001110`; decoding gives −114 × 0.176 =
−20.064, a difference of 0.064 within the tolerance of 0.088: PASS.

## The services around the decoder

- **DecodingService** subscribes to the dataframe and frame stores and
  re-decodes on every change (`dataframe`, `frame` and single-`word` events),
  writing the result to the engineering store.
- **FrameService** creates blank and random frames, loads and saves frame
  files (refusing a WPS that differs from the loaded dataframe), and edits
  single words with cell-level notification.
- **SimulationService** lists encodable parameters, applies scenarios
  through the encoder and produces the closed-loop report.

All three raise `ServiceError(state, message)` with an explicit state name
such as `INVALID_WORD`, `DATAFRAME_WPS_MISMATCH`, `ENCODE_ERROR` or
`MISSING_DATAFRAME` (see [10 — Troubleshooting](10-troubleshooting.md)).
