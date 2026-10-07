# Stage 1: build the static site
FROM node:20-alpine AS build
WORKDIR /web
COPY package.json package-lock.json* ./
RUN npm install --no-audit --no-fund
COPY . .
RUN npm run build

# Stage 2: serve it with nginx and forward /api to the backend container
FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /web/dist /usr/share/nginx/html
EXPOSE 80
