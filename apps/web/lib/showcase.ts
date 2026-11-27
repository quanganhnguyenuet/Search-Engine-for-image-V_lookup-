// Example searches with preview images taken from real results of the current corpus.
// If an image id disappears from the corpus, its preview falls back to an empty tile.
import { API_URL } from "@/lib/search";

export const imageUrl = (assetId: string) => `${API_URL}/api/v1/images/${assetId}`;

export type SearchExample = {
  kind: string;
  hint: string;
  query: string;
  images: string[];
};

export const SEARCH_EXAMPLES: SearchExample[] = [
  {
    kind: "Theo vùng đặc sản",
    hint: "Nêu địa danh để xem đặc sản của vùng đó.",
    query: "đặc sản Bến Tre",
    images: [
      "9e3c1c3295927b33273b3711549de86d128fe359e480ae822a0769d99bf06d0d",
      "59995fc2645016062e5bc19d5a2b9018b13cb9ad48476a570c0e1a690c776f3c",
      "5b2c02a3097f2ed8d6521529471c5783f216609d2bbe36eb929a3d2b4c5a2a79",
      "e78ed3802fa8eefb79c9ca1e0f95792798c0bbf22cb6f8e06524cd98d346df6e",
    ],
  },
  {
    kind: "Theo hãng",
    hint: "Xem các sản phẩm của một hãng.",
    query: "sản phẩm Shimano",
    images: [
      "a285d86212c1a2f51b97d2a60723dc495fec7e14906c5bb9cfb97dfaa89a1d93",
      "88c16abd6fd8ab821758646bc0c0d06ae473feabced064ae8cec7c2a5131e215",
      "1497e55853f4f0f1d451999ad59fd79b261e6812e1f30c1084a13e2f0be30592",
      "c36f5a6c46321b004e8cfbda54048215a89f53896b6aa9bb088459bbdb2364bd",
    ],
  },
  {
    kind: "Theo mô tả",
    hint: "Chưa biết tên? Tả loại hàng, màu sắc, đặc điểm.",
    query: "chuột không dây màu đen",
    images: [
      "4f70e98124be94f38e5f675bd28cf0cbe7cf7d07db14d835741f53617e64e22e",
      "262ab48d7c20e7959d37a968691066e2f36310d3f194c9c2f2c57aef97e9605f",
      "61e38eb805d322ad8d1c63b62d6bf0ab286572fd86882c61819920d650e973ac",
      "b24ff583c12b3dd280f4cf2ddcbd919ea2fa72c3480ce864101d4887a6604ce7",
    ],
  },
  {
    kind: "Theo tên sản phẩm",
    hint: "Gõ đúng tên hoặc mẫu bạn đang cần.",
    query: "Chuột Logitech MX Master 3",
    images: [
      "e231281323dbca67872270ff47bee50654a9bd2a98b1fdd28d39b4fc246ab803",
      "ab099cede17aa5ff9ac7e5ca3a51474040e3ae024395bbe1be6aae4ad3904ab6",
      "aad70a58866ec93a31b93a1d32bc69d2720ac8621bce1797d69b6494ba03b782",
      "87e17368ac2578117156db5f6efda357bb1ec03abac3e73b52b2447139c645b4",
    ],
  },
  {
    kind: "Nhiều sản phẩm một lúc",
    hint: "Nối các sản phẩm bằng chữ “và”.",
    query: "Kindle Paperwhite và chuột Logitech MX Master 3",
    images: [
      "a25dfe793e6ec784ce3885f0dbbbde2b2019a203e40f9c14d72af09576a22a7e",
      "e5aa3a3795b811ea8db2e1e1442a5bddf551da877bbaeececc643e44e2f1a90d",
      "8ee89bd82982d3577f19b4e250743fcf22840cb1a10d925f75742026fd963549",
      "dff18234eb221d9631bc7be45335db9ba3a9affe14a9b8b39dacffe33cac36fd",
    ],
  },
];

/** The relation drawn beside the sign-in form: a place and some of its specialities. */
export const TRACE_EXAMPLE = {
  query: "đặc sản Bến Tre",
  place: "Bến Tre",
  products: [
    { name: "Cơm dừa", image: SEARCH_EXAMPLES[0].images[0] },
    { name: "Dừa sấy", image: SEARCH_EXAMPLES[0].images[1] },
    { name: "Bánh tráng sữa", image: SEARCH_EXAMPLES[0].images[2] },
  ],
};
