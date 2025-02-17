'use client';
;
import { AdminPageHeading } from '@/components/admin-page-heading';
import { CreateDatasourceForm } from '@/components/datasource/create-datasource-form';
import { mutateKnowledgeBases, useKnowledgeBase } from '@/components/knowledge-base/hooks';
import { Loader2Icon } from 'lucide-react';
import { useRouter } from 'next/navigation';
import { use, useTransition } from 'react';

export default function NewKnowledgeBaseDataSourcePage (props: { params: Promise<{ id: string }> }) {
  const params = use(props.params);
  const id = parseInt(decodeURIComponent(params.id));
  const { knowledgeBase } = useKnowledgeBase(id);
  const [transitioning, startTransition] = useTransition();
  const router = useRouter();

  return (
    <>
      <AdminPageHeading
        breadcrumbs={[
          { title: 'Knowledge Bases', url: '/knowledge-bases', docsUrl: '/docs/knowledge-base' },
          { title: knowledgeBase?.name ?? <Loader2Icon className="size-4 animate-spin repeat-infinite" />, url: `/knowledge-bases/${id}` },
          { title: 'DataSources', url: `/knowledge-bases/${id}/data-sources` },
          { title: 'New' },
        ]}
      />
      <CreateDatasourceForm
        knowledgeBaseId={id}
        transitioning={transitioning}
        onCreated={() => {
          startTransition(() => {
            router.back();
          });
          mutateKnowledgeBases();
        }}
      />
    </>
  );
}
