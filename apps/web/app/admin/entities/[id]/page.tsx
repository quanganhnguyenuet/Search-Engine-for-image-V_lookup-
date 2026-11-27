import { EntityEditor } from "@/components/admin/entity-editor";

export default async function AdminEntityPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  // key: a new entity starts with fresh form state.
  return <EntityEditor key={id} id={decodeURIComponent(id)} />;
}
