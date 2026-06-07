/** ISO 日期（YYYY-MM-DD）→ 短标签「MM-DD」。 */
export function formatTradeDateShort(isoDate: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(isoDate);
  if (!match) {
    return isoDate;
  }
  return `${match[2]}-${match[3]}`;
}
