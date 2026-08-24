/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Fail the build on lint or type errors rather than shipping them.
  eslint: { ignoreDuringBuilds: false },
  typescript: { ignoreBuildErrors: false },
};

export default nextConfig;
