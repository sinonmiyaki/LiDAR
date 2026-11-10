# Third-party software and models

- [RapidOCR 3.9.2](https://github.com/RapidAI/RapidOCR/tree/v3.9.2) by the RapidAI contributors. Apache License 2.0. LiDAR bundles the ONNX Runtime engine with the RapidOCR Python wheel and uses its models locally. See [license](licenses/RapidOCR-Apache-2.0.txt).
- [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) by the PaddlePaddle contributors. Apache License 2.0. The packaged ONNX files are the PP-OCR mobile orientation classifier, PP-OCRv6 small detector and recognizer, and PP-OCRv4/v5 script-specific recognizers from [RapidOCR's model release](https://www.modelscope.cn/models/RapidAI/RapidOCR). LiDAR distributes these upstream ONNX files without changing their weights. See [license](licenses/PaddleOCR-Apache-2.0.txt).
- [M2M-100 418M](https://huggingface.co/facebook/m2m100_418M) by Meta AI (Angela Fan et al.), under the MIT License. LiDAR uses the [CTranslate2 int8 conversion by Vorago Studios](https://huggingface.co/VoragoStudios/gct-m2m100-418m-ct2). The upstream conversion changes the weights to int8. See [license](licenses/M2M100-MIT.txt).
- [fastText lid.176.ftz](https://fasttext.cc/docs/en/language-identification.html) by the fastText authors, distributed unchanged under [Creative Commons Attribution-ShareAlike 3.0 Unported](https://creativecommons.org/licenses/by-sa/3.0/). See the [original authors and papers](https://fasttext.cc/docs/en/language-identification.html#references).
- [Pretendard](https://github.com/orioncactus/pretendard) by Orion Cactus, SIL Open Font License 1.1. See [license](fonts/OFL.txt), also installed under `_internal/fonts`.

Other bundled Python package license and notice files are collected under `_internal/licenses` in the Windows build.
