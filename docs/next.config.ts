import type { NextConfig } from "next";
import nextra from "nextra";

const withNextra = nextra({
  // Nextra v4 config (no theme/themeConfig here; theme is applied via layout).
});

const nextConfig: NextConfig = {
  /* config options here */
  turbopack: {
    root: __dirname,
  },
};

export default withNextra(nextConfig);
