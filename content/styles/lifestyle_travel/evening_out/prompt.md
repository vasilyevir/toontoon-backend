Адаптация промпта из docs/example_prompt.md под наш путь с фотографией.

Три вещи изменены осознанно:

1. **Убрано описание человека.** В исходнике «young girl», возраст, макияж и
   черты лица. У модели уже есть снимок, и слова про внешность с ним
   конкурируют — она рисует похожего, а не того же. Осталось только то, что
   действительно меняем: место, свет, одежда, поза, кадр.
2. **Убран бренд.** «JIL SANDER» на футболке — чужой товарный знак в кадре,
   который мы показываем как своё. Осталась та же вещь без надписи.
3. **Формат 4:5 → 9:16.** Мы снимаем вертикаль; про соотношение модели говорит
   параметр запроса, поэтому из текста оно убрано вовсе, чтобы не спорить с ним.

---

the person in the photo seated relaxed and elegant in a soft chair in a cosy modern restaurant in the evening, large window with white horizontal blinds nearby, dim intimate ambient light, an oversized plain white t-shirt with no lettering, black translucent lace trousers with a large floral pattern, a small bright red structured bag on the table, gold earrings and several delicate rings and pearl bracelets, hair in soft light waves falling naturally around the face, holding an elegant glass of a golden cocktail with ice at chest level, well away from the face, hand natural and relaxed, eyes fully open, looking towards the camera with a calm confident gaze, no posed smile, shot candidly on a compact camera with direct on-camera flash while keeping the warm ambient light of the room, warm creamy beige colour grade with golden hues, delicate film grain, soft natural shadows, no other people in the frame, no text or logos anywhere
