import { defineCollection, z } from 'astro:content';
import { glob } from 'astro/loaders';

const articles = defineCollection({
  loader: glob({ pattern: '**/*.md', base: './src/content/articles' }),
  schema: z.object({
    title: z.string(),
    excerpt: z.string(),
    tag: z.string(),
    readTime: z.string(),
    publishDate: z.date(),
    draft: z.boolean().optional().default(false),
  }),
});

export const collections = { articles };
