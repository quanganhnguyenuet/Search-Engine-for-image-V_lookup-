import { ImportDetailView } from "@/components/admin/import-detail";

export default async function AdminImportPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <ImportDetailView id={decodeURIComponent(id)} />;
}
