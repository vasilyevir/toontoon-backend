Собран по той же схеме, что и промпт из docs/example_prompt.md: сцена → одежда →
волосы → макияж → поза и руки → взгляд → камера и свет → цветокоррекция →
оговорки про реализм → запреты. Тот промпт держится именно этим порядком:
сначала мир, потом человек в нём, и только в конце — как это снято.

Про поворот головы сказано дважды и в самом начале блока про позу: первая
версия просила «телом от камеры, головой назад», и модель послушалась только
первой половины — на выходе был силуэт со спины, без лица. Для карточки это
провал: весь смысл в том, что человек себя узнаёт.

Отличия от исходника те же, что и у Evening Out: не описываем внешность
(у модели уже есть снимок), не называем брендов, соотношение сторон задаёт
параметр запроса, а не текст.

---

the person in the photo standing on a curved marble staircase inside a bright modern art gallery, pale stone walls and a polished floor, a dark abstract sculpture on a plinth far behind, cool daylight falling from a high window, dressed head to toe in matte black, a fitted high-neck long-sleeve top and clean tailored lines below it, their own hair worn as it is and neatly kept off the face, no jewellery, the face clearly visible and turned to the camera, looking back over the shoulder straight into the lens, body angled away from the camera at three quarters, one hand relaxed at the side, chin slightly lowered, calm composed gaze straight into the lens, no posed smile, photographed from a high angle looking down the staircase, shot on a 50mm lens at a wide aperture in available light, deep blacks and clean bright highlights, fine film grain, monochrome black and white, no other people in the frame, no text or logos anywhere
