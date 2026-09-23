# Bundled OCR recognizer models

`en_PP-OCRv3_rec_infer.onnx` is the English PP-OCRv3 text recognizer of
PaddleOCR (PaddlePaddle), converted to ONNX by the RapidOCR project and
downloaded from https://huggingface.co/SWHL/RapidOCR (`PP-OCRv3/`).  It is
licensed under the Apache License 2.0, like PaddleOCR and RapidOCR.  The
character list (95 ASCII characters) is embedded in the model's metadata, so
no dictionary file is needed.

It is the default recognizer of the scanned-page importer
(`ImportProfile.ocr_recognizer = "en"`, see `../scan.py`): on the synthetic
benchmark (`tools/ocr_bench.py`) it reads more cells correctly than the
Chinese-plus-Latin model RapidOCR ships with and never produces CJK
punctuation such as "。" for a lone "0".  RapidOCR's own detector and its
default recognizer stay available (`ocr_recognizer = "ch"`).
