export const categories = [
  { id: "am_thuc", label: "Ẩm thực", symbol: "◒" },
  { id: "kien_truc", label: "Kiến trúc", symbol: "⌂" },
  { id: "phong_canh", label: "Phong cảnh", symbol: "△" },
  { id: "le_hoi", label: "Lễ hội", symbol: "✺" },
  { id: "trang_phuc", label: "Trang phục", symbol: "✧" },
  { id: "thu_cong_my_nghe", label: "Thủ công mỹ nghệ", symbol: "❋" },
] as const;

export type CultureAsset = {
  id: string; title: string; category: string; categoryLabel: string;
  description: string; tags: string[]; context: Record<string, string>;
  imageUrl: string; sourceUrl: string;
};

export type SearchResponse = {
  items: CultureAsset[]; total: number; page: number; pages: number;
  libraryTotal: number; counts: Record<string, number>;
};
