"use client";

import { useEffect } from "react";
import { useParams } from "next/navigation";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { rememberExperiment } from "@/lib/selection";

export default function ExportPage() {
  const params = useParams<{ id: string }>();
  const id = params.id;

  useEffect(() => {
    if (id) rememberExperiment(id);
  }, [id]);

  return (
    <div className="mx-auto grid max-w-3xl gap-6">
      <div>
        <h1 className="text-2xl font-semibold">导出</h1>
        <p className="mt-2 text-sm text-muted-foreground">压缩包由实验导出接口生成。</p>
      </div>
      <Card>
        <CardHeader>
          <CardTitle>{id}</CardTitle>
        </CardHeader>
        <CardContent>
          <Button asChild>
            <a href={`/api/experiments/${id}/export`}>下载复现包 ZIP</a>
          </Button>
        </CardContent>
      </Card>
    </div>
  );
}
