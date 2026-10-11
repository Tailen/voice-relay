# Third-party source and models

VoiceRelay's MIT license covers its own code. It does not relicense downloaded
source, dependencies or model weights. No model weights or voice recordings are
included in this repository.

- **X-VC**, Jerrister Zheng: https://github.com/Jerrister/X-VC, pinned to
  `49df8c591eafc48b096e466d96f9839f9c0dd739`. The setup script downloads the complete
  MIT-licensed source archive and preserves its license and embedded notices.
  The checkpoint comes separately from https://huggingface.co/chenxie95/X-VC.
- **GLM-4-Voice-Tokenizer**, Zhipu AI: https://huggingface.co/zai-org/glm-4-voice-tokenizer,
  pinned to `a5f2404e63c84e92f5238908e1706316324ebafa`. Its model license and README
  are downloaded alongside the tokenizer. Review those terms for your use.
- **ERes2Net speaker model**, ModelScope: https://modelscope.cn/models/iic/speech_eres2net_sv_en_voxceleb_16k.
  Each downloaded file is pinned by revision and digest in the asset manifest;
  its model card is preserved alongside the weights.

Python dependency versions and artifact hashes are in `uv.lock`; their licenses
remain those of their respective authors. The native application uses Apple's
system frameworks. The optimization code reproduces the upstream decoder's
operations and weights rather than shipping a separate trained model.

## X-VC source notice

MIT License

Copyright (c) 2026 Jerrister Zheng

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
