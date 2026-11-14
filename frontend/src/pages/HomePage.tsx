export function HomePage() {
  return (
    <>
      <div className="page-heading">
        <div>
          <p className="eyebrow">ВАШЕ ПРОСТРАНСТВО ДЛЯ ВИДЕО</p>
          <h1>Главная</h1>
        </div>
        <span className="category-label">Все видео</span>
      </div>
      <section className="empty-state" aria-labelledby="empty-title">
        <div className="empty-art" aria-hidden="true">
          <div className="video-outline"><span>▶</span></div>
          <span className="art-dot dot-one" />
          <span className="art-dot dot-two" />
        </div>
        <h2 id="empty-title">Всё начинается с первого видео</h2>
        <p>Здесь появятся новые истории, идеи и открытия.<br />Пока в ленте нет видео.</p>
      </section>
    </>
  )
}
