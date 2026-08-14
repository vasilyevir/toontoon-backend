# Анимация поля ввода промта (печатающий плейсхолдер) — 1:1

Это та самая анимация, где в поле по очереди «печатаются» разные подсказки с
мигающим курсором. В вебе она есть в двух местах, тексты разные.

Готовый Swift-компонент: `ToontoonApp/DesignSystem/TypewriterPlaceholder.swift`
(`TypewriterPlaceholder` + готовое поле `PromptComposerField`).

## Логика (машина состояний), одинаковая для обоих мест
```
старт: задержка 400 мс
цикл по фразам:
  typing   — добавлять по 1 символу каждые 60 мс, пока фраза не напечатана
  pause    — 2200 мс подождать
  deleting — удалять по 1 символу каждые 32 мс, пока не пусто
  gap      — 350 мс
  → следующая фраза (по кругу, индекс % длины)
```
Плейсхолдер показывается **только когда поле пустое И не в фокусе**. Как только
пользователь фокусит поле или что-то вводит — анимация останавливается и
плейсхолдер скрывается.

## Курсор (каретка)
- Полоска шириной **2px**, белая, радиус 1px, стоит сразу после текста.
- Мигает раз в **1 секунду** «жёстко» (CSS `step-start`): первые 0.5с видна,
  следующие 0.5с скрыта (без плавного затухания).
- Высота: composer — 22px; стартовый экран (мобилка) — 26px; лендинг (десктоп) — 44px.

## Тексты фраз
**Чат-композер (экран Generate)** — `generate/page.tsx` `PROMPTS`:
1. `Describe your idea...`
2. `Describe style references...`
3. `Enter a text prompt or upload reference photos...`
4. `A majestic dragon in 3D cartoon style...`

**Стартовый экран / лендинг** — `PromptInput.tsx` `PROMPTS`:
1. `Pixar characters...`
2. `Landscape in Studio Ghibli style...`
3. `Portrait of a mysterious girl with glowing eyes...`
4. `Vintage analog collage with retro aesthetics...`
5. `A futuristic city at sunset...`
6. `I want nature, but in the Pixar style...`

(В Swift это `Array.composerPrompts` и `Array.startScreenPrompts`.)

## Цвет/шрифт плейсхолдера
- Composer: 18/medium, цвет `#A3A3A3`, слева отступ под orb (52pt на мобиле).
- Стартовый экран (мобилка): 20/regular, цвет `#666666`.

## Как встроить
```swift
// В нижнем композере Generate:
PromptComposerField(text: $prompt, phrases: .composerPrompts) {
    send()   // Enter / Send
}

// На стартовом экране:
PromptComposerField(text: $prompt, phrases: .startScreenPrompts)
```
Или вручную: наложи `TypewriterPlaceholder(...)` в `ZStack` поверх `TextField`
и показывай, пока `text.isEmpty && !focused`.

## Крутящийся «orb»
Рядом с полем в композере слева стоит иконка `Pic.svg` (44×44 на мобиле),
`mixBlendMode: screen`, которая **вращается во время генерации** (`animate-spin`,
1.5с/оборот). В SwiftUI: `Image("Pic").rotationEffect(...)` с бесконечной
линейной анимацией, включать только при `isGenerating`.
```
