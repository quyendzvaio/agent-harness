"""A microservices-demo-like k8s repo: skaffold maps images to code dirs, Go services talk gRPC through `*_ADDR`
env set in Deployments, generated stubs sit in every service, a C# cart talks to redis, and a load generator
reaches the frontend through its command line."""

SKAFFOLD = """\
apiVersion: skaffold/v4beta6
kind: Config
build:
  artifacts:
    - image: frontend
      context: src/frontend
    - image: checkoutservice
      context: src/checkoutservice
    - image: shippingservice
      context: src/shippingservice
    - image: cartservice
      context: src/cartservice
    - image: loadgenerator
      context: src/loadgenerator
manifests:
  rawYaml:
    - ./kubernetes-manifests/*.yaml
"""


def _deployment(name: str, port: int, env: dict[str, str], command: str = "") -> str:
    env_lines = "".join(f"            - name: {k}\n              value: \"{v}\"\n" for k, v in env.items())
    cmd = f"          command: [\"sh\", \"-c\", {command!r}]\n" if command else ""
    return f"""\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {name}
spec:
  selector:
    matchLabels:
      app: {name}
  template:
    metadata:
      labels:
        app: {name}
    spec:
      containers:
        - name: server
          image: {name}
{cmd}          ports:
            - containerPort: {port}
          env:
{env_lines}---
apiVersion: v1
kind: Service
metadata:
  name: {name}
spec:
  selector:
    app: {name}
  ports:
    - port: {port}
      targetPort: {port}
"""


FRONTEND_EXTERNAL = """\
---
apiVersion: v1
kind: Service
metadata:
  name: frontend-external
spec:
  type: LoadBalancer
  selector:
    app: frontend
  ports:
    - port: 80
      targetPort: 8080
"""

REDIS = """\
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: redis-cart
spec:
  selector:
    matchLabels:
      app: redis-cart
  template:
    metadata:
      labels:
        app: redis-cart
    spec:
      containers:
        - name: redis
          image: redis:alpine
          ports:
            - containerPort: 6379
---
apiVersion: v1
kind: Service
metadata:
  name: redis-cart
spec:
  selector:
    app: redis-cart
  ports:
    - port: 6379
"""

PROTO = """\
syntax = "proto3";
package demo;

service CheckoutService {
  rpc PlaceOrder(PlaceOrderRequest) returns (PlaceOrderResponse) {}
}

service ShippingService {
  rpc ShipOrder(ShipOrderRequest) returns (ShipOrderResponse) {}
}

service CartService {
  rpc GetCart(GetCartRequest) returns (Cart) {}
}
"""

GENERATED = """\
package demo

func NewCheckoutServiceClient(cc grpc.ClientConnInterface) CheckoutServiceClient { return nil }
func RegisterCheckoutServiceServer(s grpc.ServiceRegistrar, srv CheckoutServiceServer) {}
func NewShippingServiceClient(cc grpc.ClientConnInterface) ShippingServiceClient { return nil }
func RegisterShippingServiceServer(s grpc.ServiceRegistrar, srv ShippingServiceServer) {}
func NewCartServiceClient(cc grpc.ClientConnInterface) CartServiceClient { return nil }
func RegisterCartServiceServer(s grpc.ServiceRegistrar, srv CartServiceServer) {}
"""

FRONTEND_MAIN = """\
package main

func main() {
	svc := new(frontendServer)
	mustMapEnv(&svc.checkoutSvcAddr, "CHECKOUT_SERVICE_ADDR")
	mustMapEnv(&svc.cartSvcAddr, "CART_SERVICE_ADDR")
	http.ListenAndServe(":8080", nil)
}

func (fe *frontendServer) placeOrder(ctx context.Context) {
	pb.NewCheckoutServiceClient(fe.checkoutSvcConn).PlaceOrder(ctx, req)
}

func (fe *frontendServer) cart(ctx context.Context) {
	pb.NewCartServiceClient(fe.cartSvcConn).GetCart(ctx, req)
}
"""

CHECKOUT_MAIN = """\
package main

func main() {
	svc := new(checkoutService)
	mustMapEnv(&svc.shippingSvcAddr, "SHIPPING_SERVICE_ADDR")
	srv := grpc.NewServer()
	pb.RegisterCheckoutServiceServer(srv, svc)
}

func (cs *checkoutService) quoteShipping(ctx context.Context) {
	pb.NewShippingServiceClient(cs.shippingSvcConn).ShipOrder(ctx, req)
}
"""

SHIPPING_MAIN = """\
package main

func main() {
	srv := grpc.NewServer()
	pb.RegisterShippingServiceServer(srv, &server{})
}
"""

CART_STARTUP = """\
public class Startup
{
    public void ConfigureServices(IServiceCollection services)
    {
        string redisAddress = Configuration["REDIS_ADDR"];
        services.AddStackExchangeRedisCache(options => options.Configuration = redisAddress);
    }

    public void Configure(IApplicationBuilder app)
    {
        app.UseEndpoints(endpoints => endpoints.MapGrpcService<CartService>());
    }
}
"""

CART_STORE = """\
public class RedisCartStore
{
    private readonly IDistributedCache _cache;

    public async Task AddItemAsync(string userId)
    {
        var value = await _cache.GetAsync(userId);
        await _cache.SetAsync(userId, value);
    }
}
"""

LOCUST = """\
from locust import HttpUser, task


class Shopper(HttpUser):
    @task
    def index(self):
        self.client.get("/")
"""

FILES = {
    "skaffold.yaml": SKAFFOLD,
    "kubernetes-manifests/frontend.yaml": _deployment(
        "frontend", 8080, {"CHECKOUT_SERVICE_ADDR": "checkoutservice:5050", "CART_SERVICE_ADDR": "cartservice:7070"}
    ) + FRONTEND_EXTERNAL,
    "kubernetes-manifests/checkoutservice.yaml": _deployment(
        "checkoutservice", 5050, {"SHIPPING_SERVICE_ADDR": "shippingservice:50051"}
    ),
    "kubernetes-manifests/shippingservice.yaml": _deployment("shippingservice", 50051, {}),
    "kubernetes-manifests/cartservice.yaml": _deployment("cartservice", 7070, {"REDIS_ADDR": "redis-cart:6379"}) + "---\n" + REDIS,
    "kubernetes-manifests/loadgenerator.yaml": _deployment(
        "loadgenerator", 8089, {"FRONTEND_ADDR": "frontend:80"}, 'locust --host="http://${FRONTEND_ADDR}" --headless'
    ),
    "protos/demo.proto": PROTO,
    "src/frontend/Dockerfile": "FROM golang:1.22\nCOPY . .\nRUN go build -o /src/server .\nENTRYPOINT [\"/src/server\"]\n",
    "src/frontend/main.go": FRONTEND_MAIN,
    "src/frontend/genproto/demo_grpc.pb.go": GENERATED,
    "src/checkoutservice/Dockerfile": "FROM golang:1.22\nCOPY . .\nENTRYPOINT [\"/src/checkoutservice\"]\n",
    "src/checkoutservice/main.go": CHECKOUT_MAIN,
    "src/checkoutservice/genproto/demo_grpc.pb.go": GENERATED,
    "src/shippingservice/Dockerfile": "FROM golang:1.22\nCOPY . .\nENTRYPOINT [\"/src/shippingservice\"]\n",
    "src/shippingservice/main.go": SHIPPING_MAIN,
    "src/shippingservice/genproto/demo_grpc.pb.go": GENERATED,
    "src/cartservice/Dockerfile": "FROM mcr.microsoft.com/dotnet/aspnet:8.0\nCOPY . .\nENTRYPOINT [\"/app/cartservice\"]\n",
    "src/cartservice/Startup.cs": CART_STARTUP,
    "src/cartservice/cartstore/RedisCartStore.cs": CART_STORE,
    "src/loadgenerator/Dockerfile": "FROM python:3.12-slim\nCOPY . .\nENTRYPOINT locust\n",
    "src/loadgenerator/locustfile.py": LOCUST,
}

REPO = {"owner": "acme", "repo": "shop", "meta": {"default_branch": "main"}, "files": FILES}
URL = "https://github.com/acme/shop"
