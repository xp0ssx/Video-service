const plural = new Intl.PluralRules("ru");
export function formatViews(value = 0) {
  const count = Math.max(0, Math.trunc(value));
  if (count >= 1000) {
    const million = count >= 1_000_000;
    const number = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 1 })
      .format(count / (million ? 1_000_000 : 1000));
    return `${number} ${million ? "млн" : "тыс."} просмотров`;
  }
  const form = plural.select(count);
  return `${count} ${form === "one" ? "просмотр" : form === "few" ? "просмотра" : "просмотров"}`;
}
