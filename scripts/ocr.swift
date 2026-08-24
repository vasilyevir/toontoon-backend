// Что написано на картинке — системным зрением macOS.
//
// Нужен замеру кадров: постер без заказанного слова — брак, а проверить это
// можно только чтением. Vision читает локально, бесплатно и без единой
// зависимости в питоне; ставить ради этого tesseract или тянуть облачный OCR
// значило бы платить за то, что уже стоит на машине.
//
//     swift scripts/ocr.swift кадр.jpg [ещё.jpg ...]
//
// Печатает по строке на файл: «путь<TAB>распознанное через пробел».

import AppKit
import Foundation
import Vision

for path in CommandLine.arguments.dropFirst() {
    guard let image = NSImage(contentsOfFile: path),
          let cg = image.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
        print("\(path)\t")
        continue
    }

    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    // Языки не сужаем: на постере может оказаться и имя латиницей, и слово
    // кириллицей, и то и другое сразу.
    request.recognitionLanguages = ["en-US", "ru-RU"]
    request.usesLanguageCorrection = false

    try? VNImageRequestHandler(cgImage: cg, options: [:]).perform([request])
    let lines = (request.results ?? []).compactMap { $0.topCandidates(1).first?.string }
    print("\(path)\t\(lines.joined(separator: " "))")
}
